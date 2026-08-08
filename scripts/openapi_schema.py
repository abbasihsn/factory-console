"""Print the API's OpenAPI schema to stdout, built from the source tree.

``pnpm codegen`` used to fetch ``http://127.0.0.1:8000/api/v1/openapi.json`` — whatever
process happened to be listening on that port. That is the wrong source, and it fails in
the one direction nobody notices: a dev server left running from an earlier checkout
answers happily, ``openapi-typescript`` reports success, and ``types.ts`` is regenerated
against models that no longer exist. It was caught here only because a new field went
missing from the output of a codegen that had just "succeeded".

Building the schema in-process reads the code that is on disk, so a stale answer is not
representable. This is NOT running the app: no port is bound and no lifespan starts —
:meth:`FastAPI.openapi` walks the route table and the Pydantic models and returns a dict.

The injected ports and the project root do not reach the output, which is a pure function
of the route and model definitions; they are supplied because :func:`create_app` requires
them, and the real implementations are used rather than fakes so an import cycle or a
missing dependency fails HERE instead of at boot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from factory_console.app import create_app
from factory_console.file_adapter.real import RealFileAdapter
from factory_console.file_adapter.real_writer import RealFileWriter


def main() -> None:
    """Serialize the schema to stdout as JSON."""
    app = create_app(
        RealFileAdapter(),
        version="0.0.0",
        project_root=Path.cwd(),
        file_writer=RealFileWriter(),
        write_token="unused-for-schema-generation",
    )
    json.dump(app.openapi(), sys.stdout)


if __name__ == "__main__":
    main()
