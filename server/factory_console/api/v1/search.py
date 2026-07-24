"""The ``GET /api/v1/search`` cross-ticket full-text search endpoint.

The backend half of the v1 search epic: a real cross-ticket full-text endpoint
the SPA's global search box consumes. Unlike T22's list ``?q=`` filter (a
case-insensitive substring over id+title only), this endpoint delegates to the
file-adapter's ``search_tickets`` capability (T36), which ranks ticket BODIES
too, and wraps the ranked :class:`~factory_console.domain.search.SearchHit`
records in a backend-owned :class:`SearchResponse` envelope. All request logic
lives in :class:`~factory_console.services.search_service.SearchService`, so the
handler only wires dependencies, loads the project, and delegates.

The handler does no error handling of its own — an out-of-range ``limit`` is
rejected at the FastAPI ``Query`` boundary and re-mapped to the
``validation_error`` 422 envelope by the app-level validation handler
``create_app`` registers, so it never reaches the service. A blank ``q`` is a
valid request (the service returns an empty result), not a validation error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from factory_console.api.deps import get_file_adapter
from factory_console.domain.search import SearchHit
from factory_console.file_adapter.protocol import FileAdapter
from factory_console.services.search_service import SearchService

# The package ``__init__`` owns the ``/api/v1`` prefix; this sub-router only names
# the route and its OpenAPI tag (mirrors ``api/v1/tickets.py``).
router = APIRouter(tags=["search"])


class SearchResponse(BaseModel):
    """Envelope for the search results: the ranked hits and their count."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[SearchHit]
    total: int


@router.get("/search")
async def search(
    request: Request,
    q: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    adapter: FileAdapter = Depends(get_file_adapter),
) -> SearchResponse:
    """Return the ranked :class:`SearchHit` s matching the full-text query ``q``.

    ``q`` is required; a blank or whitespace-only value yields an empty result
    (``{items: [], total: 0}``) rather than a validation error. ``limit`` bounds
    the number of hits to ``ge=1``/``le=200`` (default 50) — an out-of-range value
    is rejected at the ``Query`` boundary as the ``validation_error`` 422 envelope
    and never reaches the service. Loads the discovered project from
    ``request.app.state.project_root`` and delegates ranking to
    :class:`SearchService`; ``total`` is the number of returned items.
    """
    root: Path = request.app.state.project_root
    project = adapter.load_project(root)
    items = SearchService(adapter).search(project, q, limit=limit)
    return SearchResponse(items=items, total=len(items))
