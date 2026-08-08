# factory_v3 fixture

A project shaped the way **App Factory v3** leaves one, so the console's v3 reading
path is exercised against the real layout rather than against a hand-made
approximation of it.

What it encodes, and why each part is here:

- **Ticket content is JSON**, under `docs/planning/tickets/<sub-version>/<ID>-<slug>.json`,
  and every manifest entry declares its `path`. That is what `factory-ticket migrate`
  produces; the flat `<ticketsDir>/<id>.md` fallback is deliberately not exercised here
  because a migrated repository never uses it.
- **Milestones are sub-versions** (`v1.0`, `v1.1`) — v3's one axis. T03 sits in the later
  one on purpose, since a sub-version boundary is where the factory holds for a human.
- **T01 and T02 share `src/foundation/entry.py`** in their `critical_files`. That overlap
  is the field's whole point: it is what serializes two lanes that would otherwise edit
  one path off bases lacking each other's changes.
- **`.factory/run-state.json` carries `phase` and `subversion`** — the two v3 additions.
  T02 is `in_progress` with `phase: "reviewing"`, so "which step is this lane on?" has an
  answer; T01 and T03 carry `phase: null`, which is what a ticket not mid-lane looks like
  (null, never absent — the factory writes it explicitly).
- **`ROADMAP.md` has no checkboxes.** Under v3 a committed status marker fails the
  factory's own lint, so a fixture carrying one would be a project the factory refuses to
  run.

`.factory/` is gitignored in a real project. It is committed here because a fixture that
depended on files git will not carry is not a fixture.
