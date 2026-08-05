"""Unit tests for :class:`RunRecord`, the per-manifest-ticket composed record.

These pin the model's own contract — ``frozen``, ``extra='forbid'``, an
id-constrained ``ticketId``, and both artifact reads carried VERBATIM rather than
summarized — separately from :mod:`tests.unit.test_run_service`, which pins how
the service composes one from real files on disk. The verbatim assertion is the
load-bearing one: the reason on each source is the fact this milestone exists to
preserve, so a record that kept only "was there data" would pass every other test
here and still lose it.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_console.domain import RunRecord
from factory_console.domain.runs import ArtifactRead


def _record(**overrides: object) -> RunRecord:
    fields: dict[str, object] = {
        "ticketId": "T89",
        "result": ArtifactRead(path=Path("/proj/.factory/results/T89.json"), data={"ok": True}),
        "receipt": ArtifactRead(path=Path("/proj/.factory/receipts/T89.json"), reason="absent"),
    }
    fields.update(overrides)
    return RunRecord(**fields)  # type: ignore[arg-type]


def test_both_artifact_reads_are_carried_verbatim() -> None:
    record = _record()
    assert record.result.data == {"ok": True}
    assert record.result.reason is None
    # The other source's reason survives beside a clean read: the two are
    # independent and neither may be inferred from the other.
    assert record.receipt.reason == "absent"
    assert record.receipt.data is None


def test_each_source_keeps_its_own_distinct_reason() -> None:
    record = _record(
        result=ArtifactRead(path=Path("/proj/.factory/results/T89.json"), reason="unparseable"),
        receipt=ArtifactRead(path=Path("/proj/.factory/receipts/T89.json"), reason="absent"),
    )
    assert record.result.reason == "unparseable"
    assert record.receipt.reason == "absent"


def test_record_is_frozen() -> None:
    record = _record()
    with pytest.raises(ValidationError):
        record.ticketId = "T90"  # type: ignore[misc]


def test_record_forbids_extra_fields() -> None:
    # e.g. a caller tempted to bolt the project-wide last-stop artifact onto the
    # per-ticket record — it names no ticket and does not belong here.
    with pytest.raises(ValidationError):
        _record(lastStop=None)


def test_ticket_id_is_pattern_constrained() -> None:
    # The id is TICKET_ID_PATTERN-constrained on the record too, like every other id
    # on the domain surface, so a path separator is refused at the model boundary.
    #
    # This is NOT what keeps PathTraversal off the read path, and must not be read as
    # such. The record is built AFTER both reads (its arguments are evaluated first),
    # so its own boundary cannot gate what the readers see; and the pattern admits a
    # bare "." / "..", which reaches the readers and DOES raise. That is why
    # RealRunArtifactReader._read catches PathTraversal and degrades — see
    # test_a_path_unsafe_manifest_id_degrades_only_its_own_record. Do not delete that
    # degrade as unreachable on the strength of this test.
    with pytest.raises(ValidationError):
        _record(ticketId="../etc/passwd")
