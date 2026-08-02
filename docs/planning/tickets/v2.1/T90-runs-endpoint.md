# [T90] GET /api/v1/runs

milestone: v2.1 · track: backend · depends_on: T89 · provides: `GET /api/v1/runs` returning one run record per manifest ticket, read-only.

## Context

Third of three replacing **T81** (see T88). This ticket owns the **HTTP surface only** — routing, response shape, and the integration tests that prove the endpoint behaves as the service does.

Keeping the endpoint separate from the composition is the point of the split: T81 bundled reader, model, service and endpoint into one review unit, and the Review Chain could not converge on it in 90 minutes.

## Staged approach

1. `api/v1/runs.py`: a read-only `GET /runs`, registered in `api/v1/__init__.py`.
2. The response is the service's records, serialized. The endpoint adds no logic of its own — if it needs a decision, that decision belongs in T89's service.
3. Integration tests against a real fixture project, including the empty case.

## Critical files

- `server/factory_console/api/v1/runs.py`
- `server/factory_console/api/v1/__init__.py`
- `tests/integration/test_api_runs.py`

## Interface & data

`GET /api/v1/runs` → `200` with a list of run records. Read-only: no POST/PUT/DELETE. Additive to the generated frontend client.

## Verification

Pytest integration: a project with artifacts → 200 and records matching the service; a project with none → 200 with records naming absent sources, **not** a 404 and **not** an empty list; the route is registered; no write verb is exposed. `make lint`, `pytest`, `pnpm check` green.
