# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the factory's spend ledger, ``.factory/metrics/ledger.jsonl``.

The factory APPENDS one JSON object per lane to this file while the console is
running, which shapes the whole reader:

- :func:`find_ledger_path` answers "does this project have a ledger at all?".
  ``.factory/`` is gitignored, so a fresh clone has none — and a missing ledger
  is NOT a zero bill. That question is answered by a ``Path | None``, separately
  from :func:`read_ledger`, whose :class:`LedgerRead` may honestly hold zero
  entries for an EMPTY ledger. The two facts never share a value.
- :func:`read_ledger` parses line by line and never lets one bad line cost the
  file. A line that is not JSON, or is JSON that does not validate, is recorded
  in ``skipped`` with its line number and reason, and the read continues.
- The last line may be a partial write caught mid-append. It is attempted like
  any other line; only its POSITION (final, with no terminating newline) names
  the failure ``partial_line``. Nothing is guessed from position beyond that
  label — a bad line in the middle gets the same treatment under its own reason.
- The read is bounded: a file over :data:`MAX_LEDGER_BYTES` is not read, and the
  cap is reported as a ``file_too_large`` skip rather than short-read in silence.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from pydantic import ValidationError

from factory_console.domain.ledger import LedgerEntry, LedgerRead, SkippedLine, SkipReason

_LOGGER = logging.getLogger(__name__)

# The ledger's project-relative location. Single source of truth for WHERE the
# ledger lives; :func:`find_ledger_path` probes exactly this under a root.
LEDGER_RELATIVE_PATH = Path(".factory") / "metrics" / "ledger.jsonl"

# Hard cap on the bytes this reader will pull into memory. The ledger is appended
# to forever by a process the console does not control, so "read the whole file"
# is an unbounded read on a hot request path. 10 MiB is ~15k typical entries —
# orders of magnitude past any real ledger — so hitting it means something is
# wrong with the file, not that a project got busy. Exceeding it is REPORTED (a
# ``file_too_large`` skip), never silently truncated into a smaller, wrong bill.
MAX_LEDGER_BYTES = 10 * 1024 * 1024

# How much of an offending line lands in ``SkippedLine.excerpt``. Short enough to
# be a recognisable prefix and nothing more; see :func:`_excerpt`.
EXCERPT_MAX_CHARS = 80

# How many DETAILED ``SkippedLine`` records one read may build. MAX_LEDGER_BYTES
# bounds the input but says nothing about the line count: 10 MiB of newlines is
# ~10.5M failing lines, and a model per line is >1 GiB — the read is bounded in
# bytes and unbounded in objects. Past this cap the lines are counted into
# ``LedgerRead.skipped_omitted`` instead of materialised, so the tally stays
# exact while the memory does not grow with the corruption. 1000 is far more
# detail than any human reads and far less than any file can weaponise.
MAX_SKIPPED_LINES = 1000

# Values redacted out of an excerpt before it is truncated: any ``session_id``
# value, and any bare UUID-shaped token (the form session ids take), so a
# malformed line that happens to LEAD with its session id cannot smuggle one out
# inside the first ``EXCERPT_MAX_CHARS``.
_REDACTIONS: tuple[re.Pattern[str], ...] = (
    re.compile(r'"session_id"\s*:\s*"[^"]*"'),
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),
)
_REDACTED = '"session_id":"<redacted>"'


def find_ledger_path(project_root: Path) -> Path | None:
    """Return the project's ledger file, or ``None`` if it has none.

    ``None`` means "this project has no ledger" and MUST NOT be turned into an
    empty :class:`LedgerRead` by the caller: no ledger is an unknown bill, an
    empty ledger is a measured zero. Callers check this first and only call
    :func:`read_ledger` on a non-``None`` result.

    The node type is checked (:meth:`Path.is_file`), not mere existence, so a
    directory named ``ledger.jsonl`` resolves to ``None`` instead of to a path
    that cannot be read.
    """
    candidate = project_root / LEDGER_RELATIVE_PATH
    return candidate if candidate.is_file() else None


def read_ledger(path: Path) -> LedgerRead:
    """Parse the JSONL ledger at ``path`` into a :class:`LedgerRead`.

    NEVER raises and never abandons the file: every line that fails becomes a
    :class:`SkippedLine` (line number, reason, truncated excerpt) and the rest
    still parse. A :class:`LedgerRead` with zero ``entries`` means the file held
    no usable entry — NOT that the project has no ledger, which is
    :func:`find_ledger_path` returning ``None``.

    Reasons follow WHY a line failed, not where it sits, with one deliberate
    exception: a failure on the final line when the file has no terminating
    newline is ``partial_line``, the signature of reading mid-append.

    A file that cannot be read at all yields zero entries plus a single
    ``unreadable`` skip at line 0; one over :data:`MAX_LEDGER_BYTES` yields a
    ``file_too_large`` skip the same way — a visible gap rather than a silent
    empty bill, naming which of the two happened.

    ``skipped`` is capped at :data:`MAX_SKIPPED_LINES` detailed records so a
    pathological file cannot cost unbounded memory; anything past the cap is
    COUNTED in ``skipped_omitted`` rather than dropped, so the number of
    unreadable lines stays exact even when their excerpts do not.
    """
    try:
        size = path.stat().st_size
    except OSError as error:
        # ``%r`` on the cause, per the rule spelled out in ``run_state.py``: an
        # OSError's text carries the offending filename, the log formatter is one
        # record per line, and an unescaped newline in it would forge a record.
        _LOGGER.warning("ledger: %s could not be stat'd: %r", path, error)
        return _whole_file_skip(path, "unreadable", "ledger file could not be stat'd")
    if size > MAX_LEDGER_BYTES:
        _LOGGER.warning(
            "ledger: %s is %d bytes, over the %d-byte cap; not read",
            path,
            size,
            MAX_LEDGER_BYTES,
        )
        return _whole_file_skip(
            path,
            "file_too_large",
            f"ledger is {size} bytes, over the {MAX_LEDGER_BYTES}-byte cap; not read",
        )

    try:
        raw = path.read_bytes()
    except OSError as error:
        _LOGGER.warning("ledger: %s could not be read: %s", path, error)
        return _whole_file_skip(path, "unreadable", "ledger file could not be read")
    # Decode with replacement rather than strictly: a byte-level corruption in one
    # line must cost that line (it will not be JSON) instead of the whole file,
    # which is the same bargain every other failure mode here strikes.
    text = raw.decode("utf-8", errors="replace")
    if not text:
        return LedgerRead(path=path)

    lines = text.split("\n")
    # A file ending in a newline leaves a trailing empty element that is not a
    # line; dropping it is also what tells us the last real line WAS terminated.
    terminated = lines[-1] == ""
    if terminated:
        lines.pop()

    entries: list[LedgerEntry] = []
    skipped: list[SkippedLine] = []
    omitted = 0

    def record_skip(line_no: int, reason: SkipReason, line: str) -> None:
        """Detail the first :data:`MAX_SKIPPED_LINES` failures; count the rest.

        Past the cap the line is still TALLIED — only its excerpt is dropped — so
        a caller's count of unreadable lines stays exact however corrupt the file.
        """
        nonlocal omitted
        if len(skipped) < MAX_SKIPPED_LINES:
            skipped.append(SkippedLine(line_no=line_no, reason=reason, excerpt=_excerpt(line)))
        else:
            omitted += 1

    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\r")
        # The final line of a file with no terminating newline may be a write in
        # progress. Only its failures are relabelled; it is parsed like any other.
        unterminated_tail = line_no == len(lines) and not terminated
        try:
            document = json.loads(line)
        except (ValueError, RecursionError, MemoryError):
            # Not just JSONDecodeError: this file is written by another process,
            # and json answers pathological input with exceptions outside that
            # type (deep nesting -> RecursionError, huge document -> MemoryError).
            reason: SkipReason = "partial_line" if unterminated_tail else "not_json"
            record_skip(line_no, reason, line)
            continue
        try:
            entries.append(LedgerEntry.model_validate(document))
        except ValidationError:
            reason = "partial_line" if unterminated_tail else "invalid_entry"
            record_skip(line_no, reason, line)
    if omitted:
        _LOGGER.warning(
            "ledger: %s had %d unreadable lines; detailing the first %d",
            path,
            len(skipped) + omitted,
            MAX_SKIPPED_LINES,
        )
    return LedgerRead(path=path, entries=entries, skipped=skipped, skipped_omitted=omitted)


def _whole_file_skip(path: Path, reason: SkipReason, excerpt: str) -> LedgerRead:
    """A :class:`LedgerRead` reporting one whole-file failure at line ``0``.

    Line ``0`` names no line, which is exactly right: the failure is the file's,
    not any one line's.
    """
    return LedgerRead(path=path, skipped=[SkippedLine(line_no=0, reason=reason, excerpt=excerpt)])


def _excerpt(line: str) -> str:
    """Return a short, session-id-free prefix of ``line`` for a human to spot it by.

    Redaction runs BEFORE truncation: truncating first could leave a complete
    UUID inside the first :data:`EXCERPT_MAX_CHARS` of a line that opens with its
    session id. Truncation then bounds what an excerpt can carry at all.
    """
    redacted = line
    for pattern in _REDACTIONS:
        redacted = pattern.sub(_REDACTED, redacted)
    if len(redacted) <= EXCERPT_MAX_CHARS:
        return redacted
    return redacted[:EXCERPT_MAX_CHARS] + "…"
