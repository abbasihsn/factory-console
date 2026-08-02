"""The result type for reading ONE of the factory's per-run JSON artifacts.

Beside ``.factory/run-state.json`` the App Factory writes three more artifacts
this console observes: ``.factory/results/<ticket_id>.json`` (a lane's result
summary), ``.factory/receipts/<ticket_id>.json`` (its review receipt), and
``.factory/last-stop.json`` (why the last run stopped). All of ``.factory/`` is
gitignored, so in a fresh clone every one of them is simply absent — and each is
written by ANOTHER process, so any one of them may be a partial write, corrupt,
or the wrong shape entirely.

:class:`ArtifactRead` exists so that "there is nothing to show" always arrives
with the REASON attached. A bare ``None`` return would collapse "the factory has
never run here", "this file is there and could not be opened", and "this file is
there and is not JSON" into one value, and a UI above cannot render an honest
empty state from that — it would say "no result" for a lane whose result exists
and could not be read. This is the same rule ``domain.ledger``'s
:class:`~factory_console.domain.ledger.LedgerRead`/:class:`~factory_console.domain.ledger.SkippedLine`
pair sets for the ledger; it differs only in that a single-object artifact has no
line-by-line granularity, so the reason belongs to the whole file.

It differs from the ledger in one more way worth stating, because it is the
reason ``absent`` is a REASON here and not a separate return type: the ledger
splits "does this project have a ledger at all?"
(:func:`~factory_console.file_adapter.ledger.find_ledger_path`, a ``Path |
None``) from "what does it say?", because an empty ledger is a MEASURED zero
bill and a missing one is an unknown bill — two facts that must never share a
value. A result artifact has no such empty-but-valid form: the file is one JSON
object or it is not there, so combining the two questions into a single call
loses nothing, provided absence is named. It is named ``absent``.

These are the console's OWN result types — nothing on disk is parsed into
them — so they keep the house ``extra="forbid"``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

ArtifactSkipReason = Literal["absent", "unreadable", "unparseable", "too_large"]
"""Why an artifact yielded no data. Every reason is a WHOLE-FILE reason.

- ``absent`` — no file exists at the artifact's path. The ordinary state of a
  fresh clone (``.factory/`` is gitignored) and of any ticket the factory has not
  run yet, so it is not a degradation — but it is still reported rather than
  returned as a bare ``None``, so no caller can mistake it for a file it failed
  to read.
- ``unreadable`` — the file EXISTS and its bytes could not be read at all:
  permission denied, an I/O error, or a directory sitting where the artifact
  belongs. Deliberately distinct from ``unparseable``: nothing was ever examined,
  so naming a syntax reason would send a human hunting for malformed JSON in a
  file nothing could open.
- ``unparseable`` — the bytes WERE read and do not yield a JSON object: not valid
  JSON at all, or valid JSON whose top-level document is not a ``dict`` (a list,
  a bare string, ``null``). The file answered, unintelligibly.
- ``too_large`` — the file exceeded the reader's size cap and was NOT read.
  Reported rather than silently short-read, so an oversized artifact can never be
  rendered as a partial one.
"""


class ArtifactRead(BaseModel):
    """The result of reading ONE ``.factory`` JSON artifact: what it said, or why not.

    Exactly one of the two outcomes holds, and they are mutually exclusive by
    construction of the reader: ``reason is None`` iff ``data`` is a successfully
    parsed JSON object. When ``reason`` is set, ``data`` is ``None``; a caller may
    therefore branch on either field and reach the same answer.

    ``path`` is carried on both outcomes — including ``absent`` — because "which
    file was this about" is exactly what an operator needs to act on a reason.

    ``data`` is DELIBERATELY an untyped ``dict[str, Any]`` and not a modeled
    schema with named fields. This ticket (T88) is the reading layer only: it
    knows how to answer "does this artifact exist and is it a JSON object", and
    it has no captured real artifact to verify field names against — modeling
    fields from guesswork would ship a schema that silently rejects what the
    factory actually writes. Composing these into a per-ticket record, with named
    fields, is T89's job and belongs there. Do not "improve" this into a typed
    schema without re-reading that split.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    data: dict[str, Any] | None = None
    reason: ArtifactSkipReason | None = None
