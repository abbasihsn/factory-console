"""The console's own writable state — where the console keeps *its* data.

The sibling of :mod:`factory_console.file_adapter`, and its mirror image: that
package reads a target project's files and never writes them, while this one owns
the single SQLite store the console itself writes (the project registry, and from
v3.1 the credentials). Nothing a project owns — tickets, run-state, roadmap — is
ever copied in here; those stay read-through from the project's own files.

This module is intentionally a docstring and nothing else: **no re-exports, no
``__all__``**. Import submodules by their full path::

    from factory_console.store.location import resolve_db_path

Sibling modules land here from separate, parallel-eligible changes, and a
re-exporting ``__init__`` would make this a shared aggregation file every one of
them had to edit — and therefore conflict on.
"""
