"""Filesystem adapter: locate an App Factory project and read its plan from disk.

Modules here translate between on-disk project layout and the domain models,
consuming and returning :class:`pathlib.Path` values and raising
:class:`factory_console.errors.FactoryConsoleError` subclasses on failure.
"""
