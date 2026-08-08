"""The open sub-version — App Factory v3's one recurring human gate.

Under v3 there is exactly ONE axis: ``ticket → sub-version → main``. Tickets auto-merge
into a single ``factory/<sub-version>`` branch as each lane finishes, and the factory
then HOLDS at the sub-version PR and waits for a human to merge it. That PR is the only
recurring gate in the whole flow (App Factory v3 plan §5), and only one sub-version is
ever open at a time — strictly sequential, no stacking.

The console is the human's window onto the factory, and until now it showed nothing of
the one thing the factory stops and waits for. :class:`Subversion` is that record, read
from the top-level ``subversion`` key of ``.factory/run-state.json``
(``lib/subversion.sh``'s ``fac_subversion_set``).

**Absent is the normal state, not an error.** The record exists only while a sub-version
is open; the factory deletes it (``fac_subversion_clear``) when the branch lands on main
or a run is reset. So ``None`` means "nothing is open between cuts", which is what a
healthy project looks like most of the time — a view must render that as nothing at all,
never as an empty or broken strip.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Subversion(BaseModel):
    """The open sub-version branch the factory is accumulating tickets onto.

    Field names are the console's camelCase; the factory writes ``branch``,
    ``base_sha``, ``name`` and ``pr_url``, and the translation happens at the
    file-adapter boundary like every other wire-shape translation here.

    ``prUrl`` is ``None`` until the PR is actually opened, and that gap is meaningful
    rather than a loading state: the factory records the branch when it CUTS the
    sub-version and the url only once ``ai-gh-open-pr`` has run. A record with a branch
    and no url is a sub-version being built; one with a url is a sub-version waiting on
    a human. Those are the two things an operator most wants to tell apart, so a view
    must not render them the same way.

    The factory keeps ``pr_url`` in run-state rather than re-deriving it from ``gh``
    precisely because it cannot be recomputed — a second ``ai-gh-open-pr`` would open a
    DUPLICATE PR rather than find the first — which is also why this console only ever
    reads it. Nothing here writes to the run-state source, in any of its forms.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    branch: str
    baseSha: str | None = None
    name: str | None = None
    prUrl: str | None = None
