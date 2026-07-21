"""The FileAdapter port and its in-memory fake.

:class:`FileAdapter` is the read-only Protocol every HTTP handler depends on;
:class:`FakeFileAdapter` is a side-effect-free, in-memory implementation for
unit and integration tests. The real, filesystem-backed adapter ships
separately and satisfies the same port.
"""

from __future__ import annotations

from factory_console.file_adapter.fake import FakeFileAdapter
from factory_console.file_adapter.protocol import FileAdapter

__all__ = ["FakeFileAdapter", "FileAdapter"]
