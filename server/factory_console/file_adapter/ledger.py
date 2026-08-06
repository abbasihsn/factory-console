# READ-ONLY: this module MUST NOT write, create, or delete. Enforced by tests.
"""Read the factory's spend ledger, ``.factory/metrics/ledger.jsonl``.

The factory APPENDS one JSON object per lane to this file while the console is
running, which shapes the whole reader:

- :func:`find_ledger_path` answers "does this project have a ledger at all?".
  ``.factory/`` is gitignored, so a fresh clone has none — and a missing ledger
  is NOT a zero bill. That question is answered by a ``Path | None``, separately
  from :func:`read_ledger`, whose :class:`LedgerRead` may honestly hold zero
  entries for an EMPTY ledger. The two facts never share a value.
  There is a THIRD answer, and callers must handle it: when the node cannot be
  probed at all — or resolves OUTSIDE the project root, which this console refuses
  to read through — the function RAISES ``OSError`` rather than collapsing "I could
  not look" into the ``None`` that means "definitively not there". A caller that
  leaves it uncaught turns an unsearchable ``.factory/`` into an unmapped 500;
  one that catches it reports the bill as UNKNOWN (found, unread) — which is what
  ``api/v1/spend.py`` does. What it must never do is report ``$0.00``.
- :func:`read_ledger` parses line by line and never lets one bad line cost the
  file. A line that is not JSON, or is JSON that does not validate, is recorded
  in ``skipped`` with its line number and reason, and the read continues.
- The last line may be a partial write caught mid-append. It is attempted like
  any other line; only its POSITION (final, with no terminating newline) names
  the failure ``partial_line``. Nothing is guessed from position beyond that
  label — a bad line in the middle gets the same treatment under its own reason.
- The read is bounded: a file over :data:`MAX_LEDGER_BYTES` is not read, and the
  cap is reported as a ``file_too_large`` skip rather than short-read in silence.
- The file is OPENED ONCE and every gate — node type, size, byte bound — is applied
  to the OPENED DESCRIPTOR, via the shared
  :func:`~factory_console.file_adapter.bounded_read.read_bounded`, exactly as
  :func:`~factory_console.file_adapter.runs._read_json_artifact` uses it and for
  the same threat model: ``.factory/`` is written by a process the console does not
  control, so a ``stat`` and a later ``open`` of the same NAME are two independent
  lookups with a swap in between. Deciding from the name meant a FIFO substituted
  after the probe stat'd as ``st_size == 0``, sailed past the cap, and blocked the
  read FOREVER — and because ``get_spend`` is ``async`` and does this I/O on the
  event loop, that hung every route in the app, not just ``/spend``. See that
  module for why this sequence has exactly one copy rather than two kept in step by
  hand.

The console MUST NOT write, create, or delete anything here — a guard test
asserts this module contains no filesystem-mutating call.
"""

from __future__ import annotations

import json
import logging
import re
import stat
from pathlib import Path

from pydantic import ValidationError

from factory_console.domain.ledger import LedgerEntry, LedgerRead, SkippedLine, SkipReason
from factory_console.file_adapter.bounded_read import read_bounded
from factory_console.domain.watched_artifacts import LEDGER_RELATIVE_PATH
from factory_console.file_adapter.path_safety import (
    ABSENT_ERRNOS,
    is_regular_file,
    resolve_or_none,
    within_root,
)

_LOGGER = logging.getLogger(__name__)

# The ledger's project-relative location — IMPORTED, and re-exported here because
# ``api/v1/spend.py`` and the tests take it from this module as part of the reader's
# surface. It is defined in :mod:`~factory_console.domain.watched_artifacts`, beside
# the run-state file locations, because the WATCHER needs the same literal: this
# reader's path and the watcher's schedule being two independent literals is exactly
# how the ledger came to be read on every ``/spend`` request and observed by nobody
# (T95). One list, so the next artefact cannot repeat it.

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


class LedgerNotContained(OSError):
    """The ledger resolved OUTSIDE the project root, so this console will not read it.

    An ``OSError`` subclass on purpose. :func:`find_ledger_path` already has a third
    answer — "I could not look" — which ``api/v1/spend.py`` catches as ``OSError`` and
    renders as found-but-unread. A REFUSAL to look and an INABILITY to look are the
    same answer to that caller: the bill is UNKNOWN, and neither may be reported as
    ``$0.00``. So containment joins that case rather than inventing a second one the
    endpoint would have to learn, and the ``None`` that means "definitively no ledger"
    stays reserved for a ledger that is definitively not there.

    T101: sharing that wire answer with the size-cap and probe-failure cases is a
    DECISION, not an oversight — ``ARCHITECTURE.md:262-274``'s rule ("the
    authorization answer may be shared while the remedy differs") is normally paid
    by naming the remedy in the response; here it is paid by the ``_LOGGER.warning``
    a few lines below instead, because widening the public envelope with a
    ``not_contained`` reason is exactly the filesystem disclosure T82's coarse
    ``unreadable`` exists to avoid. The remedy still has to be findable SOMEWHERE —
    it is this log line, not the HTTP body — and ``spend/+page.svelte`` says so
    rather than guessing at causes it cannot tell apart.
    """


def find_ledger_path(project_root: Path) -> Path | None:
    """Return the project's ledger file, ``None`` if it has none, else RAISE.

    ``None`` means "this project has no ledger" and MUST NOT be turned into an
    empty :class:`LedgerRead` by the caller: no ledger is an unknown bill, an
    empty ledger is a measured zero. Callers check this first and only call
    :func:`read_ledger` on a non-``None`` result.

    The node type is checked, not mere existence, so a directory named
    ``ledger.jsonl`` resolves to ``None`` instead of to a path that cannot be read.

    "I could not look" is the THIRD answer, and it propagates as an ``OSError``
    rather than collapsing into ``None``. :meth:`Path.is_file` cannot carry that
    distinction portably — through CPython 3.12 it re-raises ``EACCES``, and from
    3.13 (gh-113978) it swallows every ``OSError`` and answers ``False`` — and
    ``pyproject.toml`` declares ``requires-python = ">=3.11"`` with no upper bound,
    so both are inside the supported range. Left to the interpreter, an unsearchable
    ``.factory/`` would report "this project has no ledger" on a new Python and
    crash the caller on an old one, from the same code. Since ``None`` here is what
    a caller renders as "$0.00, no ledger", that fail-open would be a false
    statement about real money arriving by way of an interpreter upgrade. So the
    split is made by :func:`~factory_console.file_adapter.path_safety.is_regular_file`,
    the shared HELPER that owns the same contract for the same reason — not restated
    here, so this reader and ``run_state.py``'s cannot drift on the surrounding probe
    logic the way they once could still drift while sharing only the errno constant.
    """
    candidate = project_root / LEDGER_RELATIVE_PATH
    try:
        if not is_regular_file(candidate):
            return None
    except OSError as error:
        # The one failure in this module that leaves it as an exception, so it is
        # also the one that would otherwise leave no trace: its caller answers
        # "the bill is unknown" with HTTP 200, and an operator asking why would
        # find a log naming the cause for the ``unreadable`` and ``file_too_large``
        # cases below and nothing at all for this one. ``%r`` on the cause, per the
        # rule at :func:`read_ledger` — the errno is the whole diagnostic here.
        _LOGGER.warning("ledger: %s could not be probed: %r", candidate, error)
        raise

    # CONTAINMENT, applied where the path is CHOSEN — the only place with a project
    # root to measure against. ``.factory/`` is written by a process the console does
    # not control and may be a checkout of an untrusted repository, so a symlink at
    # ``ledger.jsonl`` — or at ``.factory`` itself — resolves wherever it points, and
    # reading through it would bill this project for whatever the server can open.
    # ``O_NOFOLLOW`` in :func:`read_ledger` does NOT cover this: it refuses a symlink
    # swapped in as the final component AFTER the resolve, which is precisely the case
    # a pre-existing symlink is not. Both non-yes answers refuse, exactly as
    # :func:`~factory_console.file_adapter.runs._safe_artifact_path` refuses them:
    # ``None`` (undecidable) and ``False`` (a proven escape) alike mean this console
    # will not read the path. The refusal RAISES rather than returning ``None``,
    # because "I will not read this" is an unknown bill, never a measured zero.
    #
    # This gate binds the path this function RETURNS. :func:`read_ledger` resolves
    # again before opening, so a symlink swapped in between the two calls is outside
    # what this closes — the same open-once-and-interrogate-the-descriptor limit the
    # rest of this module works within, and a narrower window than the checked-in
    # symlink this refuses.
    resolved = resolve_or_none(candidate)
    if resolved is None or not within_root(resolved, project_root):
        _LOGGER.warning(
            "ledger: %s does not resolve inside the project root; it is not read", candidate
        )
        raise LedgerNotContained(
            f"ledger at {candidate} resolves outside the project root; not read"
        )
    return candidate


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
    # Resolved for the OPEN only; ``path`` stays the reported one, because
    # ``source.path`` is a contract with the caller about WHERE the console looked,
    # not about where the filesystem sent it. Resolving first is also what keeps a
    # symlinked ledger readable while ``O_NOFOLLOW`` below still refuses a symlink
    # swapped in AFTER this line — the same order
    # :func:`~factory_console.file_adapter.runs._read_json_artifact` uses. A path that
    # will not resolve is opened by its original name and left to fail there, rather
    # than being called absent here.
    #
    # WHERE that symlink may point is not decided here: this function takes a path and
    # has no root to measure it against. :func:`find_ledger_path` owns the containment
    # gate and refuses a ledger resolving outside the project root before any caller
    # reaches this line, so the symlink still readable here is an in-root one.
    target = resolve_or_none(path) or path

    result = read_bounded(target, max_bytes=MAX_LEDGER_BYTES, label="ledger")
    if result.outcome == "not_found":
        # :func:`find_ledger_path` already proved this path existed as a regular
        # file, so reaching "not found" here is a race (deleted between finding it
        # and reading it) rather than the ordinary absence
        # :mod:`~factory_console.file_adapter.runs` treats quietly — worth a log.
        _LOGGER.warning("ledger: %s could not be opened: no such file", path)
        return _whole_file_skip(path, "unreadable", "ledger file could not be opened")
    if result.outcome == "unreadable":
        return _whole_file_skip(path, "unreadable", "ledger file could not be read")
    if result.outcome == "too_large":
        return _whole_file_skip(
            path,
            "file_too_large",
            f"ledger is over the {MAX_LEDGER_BYTES}-byte cap; not read",
        )
    raw = result.data
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
