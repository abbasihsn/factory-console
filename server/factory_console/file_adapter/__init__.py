"""Filesystem adapter: read a target project's plan artifacts off disk.

Each module here turns one on-disk artifact (ticket ``.md`` bodies, the tickets
manifest, run-state markers) into the shared domain models, enforcing
defense-in-depth path safety so a request can never read outside the resolved
project root.
"""
