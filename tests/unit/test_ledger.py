"""Unit tests for the read-only ledger reader.

The primary fixture, :data:`REAL_ENTRY_LINE`, is a VERBATIM copy of a real line
from this repository's ``.factory/metrics/ledger.jsonl`` (quoted in the T79
ticket) — the point of the ticket is reading what the factory actually writes,
so the tests read that, not a hand-simplified stand-in. Only the elided parts of
the quoted line are filled in: the session id (redacted at the source) and the
three ``by_model`` sub-objects the quote shows as ``{...}``.

Covers the parse of a real multi-model entry, forward compatibility
(``extra="ignore"``), the three per-line skip reasons alongside good lines,
non-finite money refused per line rather than poisoning the bill, the bounded
read, the no-ledger vs empty-ledger distinction, the third answer
:func:`find_ledger_path` gives when it could not look at all, and the read-only
guard.
"""

import errno
import math
import os
from datetime import timedelta
from pathlib import Path

import pytest
from _read_only_guard import (
    assert_module_carries_read_only_header,
    assert_module_is_read_only,
)

from factory_console.domain.ledger import LedgerRead
from factory_console.file_adapter import ledger as ledger_module
from factory_console.file_adapter.ledger import (
    EXCERPT_MAX_CHARS,
    MAX_LEDGER_BYTES,
    MAX_SKIPPED_LINES,
    find_ledger_path,
    read_ledger,
)

# A real ledger line, verbatim (see the module docstring). Note it carries
# ``peak_context`` and ``sessions``, which LedgerEntry does not model — the
# factory writes more than the console reads, which is the whole reason these
# models are ``extra="ignore"``.
REAL_ENTRY_LINE = (
    '{"ts":"2026-07-30T16:33:22Z","agent":"lead","level":"ticket","ids":["T71"],'
    '"model":"sonnet","effort":"medium","wall_min":12,"turns":27,"peak_context":133027,'
    '"tokens":{"input":8546,"output":40143,"cache_read":7261803,'
    '"cache_creation":232826,"total":7543318},'
    '"cost_usd":5.740558350000003,"cost_scope":"lane",'
    '"session_id":"81dda660-3f1a-4c67-9f0e-2b7c5d9a1e04",'
    '"review_tier":null,"sessions":1,'
    '"by_model":{'
    '"claude-haiku-4-5-20251001":{"input":112,"output":903,"cache_read":0,'
    '"cache_creation":0,"cost_usd":0.0041205},'
    '"claude-sonnet-5":{"input":8434,"output":39240,"cache_read":7261803,'
    '"cache_creation":232826,"cost_usd":5.02143785},'
    '"claude-opus-4-8[1m]":{"input":0,"output":0,"cache_read":0,'
    '"cache_creation":0,"cost_usd":0.7150000}}}'
)

_LEDGER_RELATIVE = Path(".factory") / "metrics" / "ledger.jsonl"


def _write_ledger(project_root: Path, text: str) -> Path:
    """Write ``text`` as the project's ledger and return its path."""
    path = project_root / _LEDGER_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# A real multi-model entry parses, exactly
# --------------------------------------------------------------------------- #


def test_real_entry_parses_with_by_model_intact_and_exact_cost(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    result = read_ledger(path)

    assert result.skipped == [], "the real ledger line must parse with nothing skipped"
    (entry,) = result.entries
    assert entry.agent == "lead"
    assert entry.level == "ticket"
    assert entry.ids == ["T71"]
    assert entry.model == "sonnet"
    assert entry.effort == "medium"
    assert entry.wall_min == 12
    assert entry.turns == 27
    assert entry.cost_scope == "lane"
    assert entry.review_tier is None
    assert entry.ts.year == 2026 and entry.ts.month == 7 and entry.ts.day == 30
    # Exact, not approximate: this is money, and the float in the file is the
    # value the console must be able to reproduce bit for bit.
    assert entry.cost_usd == 5.740558350000003
    assert entry.tokens.input == 8546
    assert entry.tokens.output == 40143
    assert entry.tokens.cache_read == 7261803
    assert entry.tokens.cache_creation == 232826
    assert entry.tokens.total == 7543318
    # by_model intact: every model id kept, verbatim, with its own spend.
    assert set(entry.by_model) == {
        "claude-haiku-4-5-20251001",
        "claude-sonnet-5",
        "claude-opus-4-8[1m]",
    }
    assert entry.by_model["claude-sonnet-5"].cost_usd == 5.02143785
    assert entry.by_model["claude-sonnet-5"].cache_read == 7261803
    assert entry.by_model["claude-haiku-4-5-20251001"].output == 903


def test_session_id_is_parsed_but_is_not_part_of_any_api_projection(tmp_path: Path) -> None:
    # The field is read so the record round-trips faithfully in process, and is
    # excluded from serialisation so it cannot leak through an API layer later.
    # This repo returns domain models straight out of its endpoints, so the
    # second assertion is the one doing the work: without it, a future
    # ``-> LedgerEntry`` would put a session id on the wire and nothing would say.
    path = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    (entry,) = read_ledger(path).entries

    assert entry.session_id == "81dda660-3f1a-4c67-9f0e-2b7c5d9a1e04"
    assert "session_id" not in entry.model_dump(), "a dumped entry must carry no session id"
    assert "81dda660" not in entry.model_dump_json(), "nor may the JSON form"


def test_a_timestamp_without_an_offset_is_read_as_utc(tmp_path: Path) -> None:
    # The factory writes "...Z" today. If it ever omits the marker, the value
    # must still be an instant: a naive datetime would parse fine here and fail
    # much later, the first time a consumer compared it against a console-built
    # tz-aware one.
    line = '{"ts":"2026-07-30T16:33:22","agent":"lead","level":"ticket","cost_usd":1.0}'
    path = _write_ledger(tmp_path, line + "\n")

    (entry,) = read_ledger(path).entries

    assert entry.ts.tzinfo is not None, "every ts must be comparable"
    assert entry.ts.utcoffset() == timedelta(0)


def test_a_timestamp_with_an_offset_keeps_it(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    (entry,) = read_ledger(path).entries

    assert entry.ts.utcoffset() == timedelta(0), "the factory's Z is UTC and stays UTC"


# --------------------------------------------------------------------------- #
# extra="ignore": tomorrow's factory field must not break today's console
# --------------------------------------------------------------------------- #


def test_unknown_top_level_field_still_parses(tmp_path: Path) -> None:
    # The regression this guards: someone "tidies" the models to extra="forbid"
    # like every other domain model, and the next factory release makes every
    # line an invalid_entry.
    line = (
        '{"ts":"2026-07-30T16:33:22Z","agent":"lead","level":"ticket","ids":["T71"],'
        '"cost_usd":1.5,"a_field_the_factory_adds_tomorrow":{"nested":true}}'
    )
    path = _write_ledger(tmp_path, line + "\n")

    result = read_ledger(path)

    assert result.skipped == [], "an unknown top-level field must not skip the entry"
    (entry,) = result.entries
    assert entry.cost_usd == 1.5
    assert not hasattr(entry, "a_field_the_factory_adds_tomorrow"), (
        "the unknown field must be ignored, not stored"
    )


def test_unknown_nested_field_still_parses(tmp_path: Path) -> None:
    line = (
        '{"ts":"2026-07-30T16:33:22Z","agent":"lead","level":"ticket","cost_usd":1.0,'
        '"tokens":{"input":5,"thinking":9},'
        '"by_model":{"claude-sonnet-5":{"input":5,"cost_usd":0.5,"reasoning_tokens":3}}}'
    )
    path = _write_ledger(tmp_path, line + "\n")

    (entry,) = read_ledger(path).entries

    assert entry.tokens.input == 5
    assert entry.tokens.total == 0, "an unwritten count must default to 0, not fail"
    assert entry.by_model["claude-sonnet-5"].cost_usd == 0.5


# --------------------------------------------------------------------------- #
# Bad lines cost one entry, never the file
# --------------------------------------------------------------------------- #


def test_not_json_line_is_skipped_and_the_good_lines_still_parse(tmp_path: Path) -> None:
    path = _write_ledger(
        tmp_path, f"{REAL_ENTRY_LINE}\nthis is not json at all\n{REAL_ENTRY_LINE}\n"
    )

    result = read_ledger(path)

    assert len(result.entries) == 2, "a bad line must cost one entry, not the file"
    (skip,) = result.skipped
    assert skip.line_no == 2
    assert skip.reason == "not_json"
    assert "not json" in skip.excerpt


def test_invalid_entry_line_is_skipped_with_its_own_reason(tmp_path: Path) -> None:
    # Valid JSON, but no ts/agent/level/cost_usd — a record, not a spend record.
    invalid = '{"agent":"lead","level":"ticket"}'
    path = _write_ledger(tmp_path, f"{REAL_ENTRY_LINE}\n{invalid}\n{REAL_ENTRY_LINE}\n")

    result = read_ledger(path)

    assert len(result.entries) == 2
    (skip,) = result.skipped
    assert skip.line_no == 2
    assert skip.reason == "invalid_entry", "valid JSON that fails validation is not 'not_json'"


def test_a_non_finite_cost_costs_its_own_line_and_not_the_whole_bill(tmp_path: Path) -> None:
    # ``json.loads`` accepts the bare literals NaN/Infinity (Python's own
    # json.dumps writes them for non-finite floats) and pydantic admits them into
    # a float by default. Left alone, one such line validates and math.fsum then
    # carries the NaN into the project total, every ticket row that entry touches,
    # and the by-cost ordering — a poisoned figure presented as MEASURED, with an
    # empty ``skipped`` list saying nothing was wrong. So the line is refused at
    # parse time, where it becomes a visible skip like any other bad line.
    for literal in ("NaN", "Infinity", "-Infinity"):
        poisoned = REAL_ENTRY_LINE.replace('"cost_usd":5.740558350000003', f'"cost_usd":{literal}')
        assert poisoned != REAL_ENTRY_LINE, "the fixture's cost field must have been substituted"
        path = _write_ledger(tmp_path, f"{REAL_ENTRY_LINE}\n{poisoned}\n")

        result = read_ledger(path)

        assert len(result.entries) == 1, f"the good line still parses alongside {literal}"
        assert math.isfinite(result.entries[0].cost_usd)
        (skip,) = result.skipped
        assert skip.line_no == 2
        assert skip.reason == "invalid_entry", f"{literal} is a corrupt value, not a syntax error"


def test_a_non_finite_by_model_cost_is_refused_the_same_way(tmp_path: Path) -> None:
    # The per-model figures are summed into the by-model cut exactly as the
    # entry's own cost is summed into the total, so they take the same guard.
    poisoned = REAL_ENTRY_LINE.replace('"cost_usd":5.02143785', '"cost_usd":NaN')
    assert poisoned != REAL_ENTRY_LINE, "the fixture's by_model cost must have been substituted"
    path = _write_ledger(tmp_path, f"{poisoned}\n")

    result = read_ledger(path)

    assert result.entries == []
    (skip,) = result.skipped
    assert skip.reason == "invalid_entry"


def test_truncated_final_line_without_newline_is_a_partial_line(tmp_path: Path) -> None:
    # The live-append case: the factory was mid-write when the console read.
    truncated = REAL_ENTRY_LINE[:120]
    path = _write_ledger(tmp_path, f"{REAL_ENTRY_LINE}\n{REAL_ENTRY_LINE}\n{truncated}")

    result = read_ledger(path)

    assert len(result.entries) == 2, "the two complete lines before it must still parse"
    (skip,) = result.skipped
    assert skip.line_no == 3
    assert skip.reason == "partial_line"


def test_a_bad_middle_line_is_not_called_partial_line(tmp_path: Path) -> None:
    # Reason follows WHY a line failed, not where it sits: only the unterminated
    # FINAL line earns the partial_line label.
    truncated = REAL_ENTRY_LINE[:120]
    path = _write_ledger(tmp_path, f"{truncated}\n{REAL_ENTRY_LINE}\n")

    result = read_ledger(path)

    assert len(result.entries) == 1
    (skip,) = result.skipped
    assert skip.line_no == 1
    assert skip.reason == "not_json", "an identical bad line mid-file is not a partial write"


def test_a_terminated_final_bad_line_is_not_partial(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, f"{REAL_ENTRY_LINE}\n{REAL_ENTRY_LINE[:120]}\n")

    (skip,) = read_ledger(path).skipped

    assert skip.reason == "not_json", "a terminated line was fully written; it is just bad"


def test_every_bad_line_is_recorded_none_dropped(tmp_path: Path) -> None:
    path = _write_ledger(
        tmp_path,
        "\n".join(["nope", '{"agent":"lead"}', REAL_ENTRY_LINE, "{oops"]) + "\n",
    )

    result = read_ledger(path)

    assert len(result.entries) == 1
    assert [(s.line_no, s.reason) for s in result.skipped] == [
        (1, "not_json"),
        (2, "invalid_entry"),
        (4, "not_json"),
    ]


def test_excerpt_is_truncated_and_never_carries_a_full_session_id(tmp_path: Path) -> None:
    # A malformed line that LEADS with its session id — the case a naive
    # "first N characters" excerpt would leak.
    session_id = "81dda660-3f1a-4c67-9f0e-2b7c5d9a1e04"
    path = _write_ledger(tmp_path, f'{{"session_id":"{session_id}","ts":"nope",oops\n')

    (skip,) = read_ledger(path).skipped

    assert session_id not in skip.excerpt, "an excerpt must never carry a full session id"
    assert len(skip.excerpt) <= EXCERPT_MAX_CHARS + 1, "an excerpt must be truncated"


def test_a_long_bad_line_is_truncated(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, "x" * (EXCERPT_MAX_CHARS * 5) + "\n")

    (skip,) = read_ledger(path).skipped

    assert len(skip.excerpt) <= EXCERPT_MAX_CHARS + 1


# --------------------------------------------------------------------------- #
# The absence rule: no ledger vs an empty ledger
# --------------------------------------------------------------------------- #


def test_find_ledger_path_returns_none_when_the_project_has_no_ledger(tmp_path: Path) -> None:
    assert find_ledger_path(tmp_path) is None, (
        "a project with no .factory/metrics/ledger.jsonl must yield None"
    )


def test_find_ledger_path_finds_the_ledger(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")

    assert find_ledger_path(tmp_path) == path


def test_find_ledger_path_ignores_a_directory_at_the_ledger_path(tmp_path: Path) -> None:
    (tmp_path / _LEDGER_RELATIVE).mkdir(parents=True)

    assert find_ledger_path(tmp_path) is None, "a directory is not a readable ledger"


def test_a_file_where_a_parent_directory_belongs_is_absence_not_a_failure(tmp_path: Path) -> None:
    # ENOTDIR: ``.factory/metrics`` is a regular file, so the ledger path cannot
    # resolve a directory component. That is a definitive "not there" — it is in
    # ``_ABSENT_ERRNOS`` — and must NOT reach the caller as the raise below.
    metrics = tmp_path / ".factory" / "metrics"
    metrics.parent.mkdir(parents=True)
    metrics.write_text("not a directory", encoding="utf-8")

    assert find_ledger_path(tmp_path) is None


def test_a_probe_that_could_not_look_raises_rather_than_reporting_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # THE reason this function stat()s by hand instead of calling Path.is_file().
    # An unsearchable .factory/ is "I could not look", and ``None`` here is what a
    # caller renders as "$0.00, no ledger" — so collapsing the two would be a false
    # statement about real money. Path.is_file() cannot carry the distinction
    # across the supported interpreter range (it re-raises EACCES through 3.12 and
    # swallows every OSError from 3.13), which is exactly why this is asserted at
    # the reader, not only through the endpoint that catches it.
    ledger = _write_ledger(tmp_path, REAL_ENTRY_LINE + "\n")
    real_stat = Path.stat

    def deny(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == ledger:
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny)

    with pytest.raises(PermissionError):
        find_ledger_path(tmp_path)


def test_a_path_the_os_cannot_even_encode_is_absence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Parity with Path.is_file(), which reads a non-encodable path as absent
    # rather than as a failure to look. ValueError is not an OSError, so it needs
    # its own arm; without one it would escape as an unmapped 500.
    def unencodable(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        raise ValueError("embedded null byte")

    monkeypatch.setattr(Path, "stat", unencodable)

    assert find_ledger_path(tmp_path) is None


def test_no_ledger_and_an_empty_ledger_are_distinguishable(tmp_path: Path) -> None:
    # THE absence rule. A fresh clone (.factory/ is gitignored) has no ledger,
    # which is an UNKNOWN bill; an empty ledger is a MEASURED zero. If these two
    # ever collapse to the same value, the UI above can truthfully render "this
    # cost nothing" for a project whose spend was simply never observed.
    no_ledger_project = tmp_path / "fresh_clone"
    no_ledger_project.mkdir()
    empty_ledger_project = tmp_path / "empty_ledger"
    empty_ledger_project.mkdir()
    empty_path = _write_ledger(empty_ledger_project, "")

    absent = find_ledger_path(no_ledger_project)
    present = find_ledger_path(empty_ledger_project)

    assert absent is None
    assert present == empty_path

    empty_read = read_ledger(present)
    assert isinstance(empty_read, LedgerRead), "an empty ledger still produces a LedgerRead"
    assert empty_read.entries == []
    assert empty_read.skipped == []
    assert empty_read.path == empty_path
    # Different types from different calls — no value they can be confused by.
    assert absent != empty_read
    assert empty_read is not None


def test_a_ledger_of_only_newlines_reports_its_blank_lines(tmp_path: Path) -> None:
    # Zero entries, but not silently: even a blank line is recorded, so "empty"
    # and "unparseable" stay tellable apart.
    path = _write_ledger(tmp_path, "\n\n")

    result = read_ledger(path)

    assert result.entries == []
    assert [s.line_no for s in result.skipped] == [1, 2]


# --------------------------------------------------------------------------- #
# The read is bounded
# --------------------------------------------------------------------------- #


def test_over_cap_file_is_reported_not_silently_short_read(tmp_path: Path) -> None:
    path = tmp_path / _LEDGER_RELATIVE
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.write(REAL_ENTRY_LINE.encode("utf-8") + b"\n")
        # Sparse-extend past the cap rather than materialising 10 MiB of bytes.
        handle.truncate(MAX_LEDGER_BYTES + 1)

    result = read_ledger(path)

    assert result.entries == [], "over the cap, nothing is read — not a partial bill"
    (skip,) = result.skipped
    assert skip.reason == "file_too_large", (
        "the cap must be recorded as a reason, never a silent short read"
    )
    assert str(MAX_LEDGER_BYTES) in skip.excerpt


def test_an_unreadable_file_is_reported_as_unreadable_not_as_bad_json(tmp_path: Path) -> None:
    # A directory stat's fine and then fails to read — the shape of any I/O
    # failure (permission denied, deleted mid-read). The reason must say the file
    # could not be read, not that its contents were malformed: nothing ever read
    # the contents, and "not_json" would send a human hunting a syntax error in a
    # file nothing could open.
    path = tmp_path / _LEDGER_RELATIVE
    path.mkdir(parents=True)

    result = read_ledger(path)

    assert result.entries == []
    (skip,) = result.skipped
    assert skip.line_no == 0, "a whole-file failure belongs to no line"
    assert skip.reason == "unreadable"


def test_skipped_lines_are_capped_but_still_counted(tmp_path: Path) -> None:
    # MAX_LEDGER_BYTES bounds the file's BYTES, not its line count — a corrupt
    # file of short bad lines is under the byte cap and still one model per line.
    # Past the cap the detail stops; the tally must not.
    bad_lines = MAX_SKIPPED_LINES + 250
    path = _write_ledger(tmp_path, "nope\n" * bad_lines)

    result = read_ledger(path)

    assert result.entries == []
    assert len(result.skipped) == MAX_SKIPPED_LINES, "detail is bounded"
    assert result.skipped_omitted == 250, "the rest are counted, never silently dropped"
    assert len(result.skipped) + result.skipped_omitted == bad_lines, (
        "the count of unreadable lines stays exact however corrupt the file"
    )


def test_an_ordinary_read_omits_nothing(tmp_path: Path) -> None:
    path = _write_ledger(tmp_path, f"{REAL_ENTRY_LINE}\nnope\n")

    result = read_ledger(path)

    assert len(result.skipped) == 1
    assert result.skipped_omitted == 0, "the cap must not perturb a normal read"


def test_a_file_at_the_cap_is_still_read(tmp_path: Path) -> None:
    line = (REAL_ENTRY_LINE + "\n").encode("utf-8")
    path = tmp_path / _LEDGER_RELATIVE
    path.parent.mkdir(parents=True)
    path.write_bytes(line + b" " * (MAX_LEDGER_BYTES - len(line)))

    result = read_ledger(path)

    assert len(result.entries) == 1, "exactly at the cap is under the cap"


# --------------------------------------------------------------------------- #
# GUARD: the read-only invariant — the module has no FS-mutating call
# --------------------------------------------------------------------------- #


def test_module_source_has_no_filesystem_mutation() -> None:
    assert_module_is_read_only(ledger_module)


def test_module_source_carries_the_read_only_header() -> None:
    assert_module_carries_read_only_header(ledger_module)
