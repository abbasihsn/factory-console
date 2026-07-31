"""The resolved target project that a single request reads through.

A :class:`Project` is constructed once per request by the file-adapter's
``load_project`` from a discovered project root. It carries only resolved paths
plus the discovery timestamp — never file contents, which live in memory only
for the duration of the request.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from factory_console.domain.run_state_source import RunStateSource


class Project(BaseModel):
    """Resolved paths for one App Factory project, discovered per request.

    ``roadmapPath``, ``runStateDir`` and ``runStateSource`` are ``None`` when the
    corresponding file or directory is absent from the target project (all are
    optional in the project layout).

    ``runStateSource`` is the resolved run-state artifact — the factory's
    ``.factory/run-state.json`` OR a legacy marker directory — and is what
    run-state reads dispatch on. ``runStateDir`` keeps its original meaning
    exactly: a path ONLY when the resolved source is a directory, so a
    JSON-sourced project has ``runStateDir is None``. Read run-state through
    ``runStateSource``; ``runStateDir`` answers only "which directory, if any, is
    off-limits to the writer".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rootPath: Path
    ticketsManifestPath: Path
    ticketsDir: Path
    roadmapPath: Path | None = None
    runStateDir: Path | None = None
    runStateSource: RunStateSource | None = None
    discoveredAt: datetime

    @model_validator(mode="before")
    @classmethod
    def _reconcile_run_state_dir_and_source(cls, data: Any) -> Any:
        """Keep ``runStateDir`` and ``runStateSource`` describing the SAME fact.

        The two fields describe one fact and MUST NOT contradict each other, so
        this reconciles them in BOTH directions and rejects a pair that cannot be
        reconciled — rather than storing the contradiction, which would let
        callers reading different fields authorize against different artifacts
        (the write gate and ``read_run_state`` read ``runStateSource``, while the
        writer's forbidden-path guard also consults ``runStateDir``).

        - ``runStateDir`` only (as every pre-``runStateSource`` construction site
          does) means "this project's run-state is that marker directory": the
          directory source is derived. Leaving ``runStateSource`` ``None`` would
          make every source-aware read answer ``unknown`` and, at the write gate,
          wave through edits to tickets a factory lane owns.
        - A ``directory`` ``runStateSource`` only means the project HAS that
          marker directory: ``runStateDir`` is filled from it, so adopting the new
          field without the old one cannot silently drop the directory from the
          writer's forbidden paths.
        - A ``json`` source has no marker directory, so ``runStateDir`` stays
          ``None`` — its documented meaning.

        Raises:
            ValueError: if both fields are supplied and disagree — a ``json``
                source alongside a non-``None`` ``runStateDir``, or a
                ``directory`` source whose ``path`` is not that ``runStateDir``.
        """
        if not isinstance(data, dict):
            return data
        source, run_state_dir = data.get("runStateSource"), data.get("runStateDir")
        if source is None:
            if run_state_dir is None:
                return data
            return {
                **data,
                "runStateSource": RunStateSource(kind="directory", path=run_state_dir),
            }
        # Normalise BOTH accepted input shapes before comparing. A ``mode="before"``
        # validator sees raw input, and ``runStateSource`` arrives as a MAPPING on
        # every deserialization path — ``model_validate``, a ``model_dump()``
        # round-trip, the REST layer — not just as an instance. Reconciling only
        # the instance form would silently skip this check for exactly the
        # untrusted, over-the-wire path it matters most on.
        if isinstance(source, RunStateSource):
            resolved = source
        elif isinstance(source, Mapping):
            try:
                resolved = RunStateSource.model_validate(source)
            except ValidationError:
                return data  # let Pydantic report it against ``runStateSource``
        else:
            return data
        if resolved.kind != "directory":
            if run_state_dir is not None:
                raise ValueError(
                    f"runStateDir {str(run_state_dir)!r} contradicts the {resolved.kind!r} "
                    f"runStateSource at {str(resolved.path)!r}: a non-directory source "
                    f"has no run-state directory"
                )
            return data
        if run_state_dir is None:
            return {**data, "runStateDir": resolved.path}
        if not isinstance(run_state_dir, str | Path):
            return data  # let Pydantic report the type against ``runStateDir``
        if Path(run_state_dir) != resolved.path:
            raise ValueError(
                f"runStateDir {str(run_state_dir)!r} contradicts the directory "
                f"runStateSource at {str(resolved.path)!r}: they must name the same directory"
            )
        return data
