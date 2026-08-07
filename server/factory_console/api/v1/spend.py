"""The ``GET /api/v1/spend`` endpoint: what the factory cost, three ways.

Turns T79's ledger read into the cuts an operator actually asks for — what did
this ticket cost, where did the money go by model, and how much of it was review
rather than build — over the existing v1 seam.

The root is the SELECTED project's, resolved per request by
:func:`~factory_console.api.deps.get_current_project_root`, not the one ``create_app``
pinned at boot; in pinned mode the two are the same path. Off that root the handler
calls ``file_adapter/ledger.py``'s :func:`find_ledger_path`/:func:`read_ledger`
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

Probing the ledger, reading it, and aggregating what it held all scale with the file —
the reader stats ``.factory/metrics/ledger.jsonl`` and then parses it line by line — so
all three are awaited through ``anyio.to_thread.run_sync`` rather than run inline on the
event loop, per ``ARCHITECTURE.md``'s Cross-cutting **Concurrency** rule. ``run_sync``
propagates the worker's exception unchanged, so the ``OSError`` contract below reads
exactly as it did when the probe ran inline. The two ``aggregate([])`` calls on the
absent and unread branches stay inline deliberately: they fold NO entries, so a thread
hop would buy nothing and only add latency to the two cheapest answers.

It raises nothing of its own; the only errors that leave here are the selection
seam's ``no_project_selected``/``selected_project_unavailable`` 409s, raised by
:func:`get_current_project_root` before the handler body runs and rendered by the
registered domain-error handler. An unreadable or over-cap ledger is not an error
here either: T79 reports it as a skipped line, which this projects into
``skipped`` so a partial total is visibly partial instead of quietly wrong. That
case also clears ``source.read`` — a file that was found and never opened reports
zero entries exactly like an empty one, so without that flag ``found: true`` over
zeroed totals would make the same "measured zero" claim about an unknown bill
that the missing-ledger branch above exists to avoid.

A ledger that cannot even be PROBED joins that same case rather than the missing
one. ``find_ledger_path`` reserves ``None`` for a ledger that is definitively not
there and RAISES when it could not tell, precisely so this endpoint cannot read
the second as the first — an unsearchable ``.factory/`` billed as "$0.00, no
ledger" would be the same false statement about real money, arriving by way of a
directory mode instead of a missing file. The raise is caught here and answered
as found-but-unread. A ledger that resolves OUTSIDE the project root raises the
same way and gets the same answer: the console refuses to read through it, and a
refusal to look leaves the bill exactly as unknown as an inability to look does.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import anyio.to_thread
from fastapi import APIRouter, Depends

from factory_console.api.deps import get_current_project_root
from factory_console.domain.spend import (
    SkippedLineInfo,
    SourceInfo,
    SpendResponse,
    was_read,
)
from factory_console.domain.spend_calc import aggregate
from factory_console.file_adapter.ledger import (
    LEDGER_RELATIVE_PATH,
    find_ledger_path,
    read_ledger,
)

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag.
router = APIRouter(tags=["spend"])


@router.get("/spend")
async def get_spend(root: Path = Depends(get_current_project_root)) -> SpendResponse:
    """Return the SELECTED project's aggregated spend, or an explicit "no ledger" body.

    Reads the ledger off the per-request ``root``. With no ledger the response is
    ``source.found: false`` over zeroed totals; with one, it is the aggregate of
    every entry that parsed, plus the line numbers and reasons of those that did
    not. A ledger that exists but could not be read at all is the third case, and
    says so with ``source.read: false`` rather than passing its zeroed totals off
    as a measurement. The ledger's ``excerpt`` and ``session_id`` are projected
    nowhere. The probe, the read, and the aggregation over what it returned are all
    awaited off the event loop.
    """
    try:
        path = await anyio.to_thread.run_sync(partial(find_ledger_path, root))
    except OSError:
        # ``find_ledger_path`` answers "I could not look" by RAISING rather than by
        # returning ``None``, so that case cannot be mistaken here for the absence
        # that ``None`` means. This is the endpoint's half of that contract: raising
        # on out of here would leave an unmapped 500, which this module promises not
        # to do, so the probe failure is turned into the one body that already says
        # "the bill is unknown" — found, not read, reason at line 0. That needs no
        # new wire vocabulary, because a client must already handle it for the
        # over-cap and unreadable cases.
        return SpendResponse.from_report(
            aggregate([]),
            source=SourceInfo(found=True, read=False, path=str(root / LEDGER_RELATIVE_PATH)),
            skipped=[SkippedLineInfo(lineNo=0, reason="unreadable")],
        )
    if path is None:
        # ``path`` is reported even though nothing was found, because the view for
        # this case (T84) has to say WHERE the console looked — that is the whole
        # explanation for a fresh clone, and the alternative is the frontend
        # hardcoding ``LEDGER_RELATIVE_PATH`` on the other side of the language
        # boundary, where it drifts the first time the factory moves the file.
        # ``found`` remains the field that says whether it is there.
        return SpendResponse.from_report(
            aggregate([]),
            source=SourceInfo(found=False, path=str(root / LEDGER_RELATIVE_PATH)),
        )

    result = await anyio.to_thread.run_sync(partial(read_ledger, path))
    return SpendResponse.from_report(
        await anyio.to_thread.run_sync(partial(aggregate, result.entries)),
        source=SourceInfo(found=True, read=was_read(result), path=str(result.path)),
        skipped=[
            SkippedLineInfo(lineNo=line.line_no, reason=line.reason) for line in result.skipped
        ],
        skipped_omitted=result.skipped_omitted,
    )
