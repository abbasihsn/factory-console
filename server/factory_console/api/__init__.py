"""HTTP edge package: dependency-injection seams and cross-cutting handlers.

Holds the FastAPI-facing glue between the transport layer and the framework-free
domain/file-adapter code: the :func:`~factory_console.api.deps.get_file_adapter`
dependency that handlers resolve via ``Depends(...)`` and the exception-mapping
registration in :mod:`~factory_console.api.error_handlers`. Kept intentionally
thin so subsequent endpoint tickets can append their sub-routers here without a
churny merge.
"""

from __future__ import annotations

from factory_console.api.deps import get_file_adapter, get_file_watcher

__all__ = ["get_file_adapter", "get_file_watcher"]
