"""The ``GET /api/v1/spend`` endpoint: what the factory cost, three ways.

Turns T79's ledger read into the cuts an operator actually asks for — what did
this ticket cost, where did the money go by model, and how much of it was review
rather than build — over the existing v1 seam.

The handler reads the discovered project root that ``create_app`` stashed on
``app.state.project_root`` (a ``Path`` guaranteed present at boot) and calls
``file_adapter/ledger.py``'s :func:`find_ledger_path`/:func:`read_ledger`
DIRECTLY, rather than through ``Depends(get_file_adapter)``. Those two are plain
functions over a project root and are deliberately not on the
:class:`~factory_console.file_adapter.protocol.FileAdapter` protocol; widening
that protocol — and every fake implementing it — for a read this endpoint is the
only consumer of would be a larger change than the endpoint itself.

All of the endpoint's care goes into one distinction. A missing ledger is NOT a
zero bill: ``.factory/`` is gitignored, so having no ledger is the NORMAL state of
a fresh clone, and "$0.00" there is a false statement about real money. So the two
cases take different paths to different bodies — ``source.found`` says which —
even though both can report zero dollars. The zeroed body for a missing ledger
still comes from ``aggregate([])`` so its shape is the shape a client already
parses, rather than a second, hand-built one that could drift.

It raises nothing of its own. An unreadable or over-cap ledger is not an error
here either: T79 reports it as a skipped line, which this projects into
``skipped`` so a partial total is visibly partial instead of quietly wrong.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from factory_console.domain.spend import SkippedLineInfo, SourceInfo, SpendResponse
from factory_console.domain.spend_calc import aggregate
from factory_console.file_adapter.ledger import find_ledger_path, read_ledger

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["spend"])


@router.get("/spend")
async def get_spend(request: Request) -> SpendResponse:
    """Return the project's aggregated spend, or an explicit "no ledger" body.

    Reads the discovered root from ``request.app.state.project_root`` — a ``Path``
    ``create_app`` requires at boot. With no ledger the response is
    ``source.found: false`` over zeroed totals; with one, it is the aggregate of
    every entry that parsed, plus the line numbers and reasons of those that did
    not. The ledger's ``excerpt`` and ``session_id`` are projected nowhere.
    """
    root: Path = request.app.state.project_root
    path = find_ledger_path(root)
    if path is None:
        return SpendResponse.from_report(aggregate([]), source=SourceInfo(found=False))

    result = read_ledger(path)
    return SpendResponse.from_report(
        aggregate(result.entries),
        source=SourceInfo(found=True, path=str(result.path)),
        skipped=[
            SkippedLineInfo(lineNo=line.line_no, reason=line.reason) for line in result.skipped
        ],
        skipped_omitted=result.skipped_omitted,
    )
