# Testing

## API (`apps/api`)

Run from `apps/api` with the venv's interpreter (`.venv\Scripts\python.exe` on Windows):

| Check | Command |
|---|---|
| Lint | `python -m ruff check .` |
| Format | `python -m ruff format --check .` |
| Types (strict, app code) | `python -m mypy` |
| Types (tests, migrations) | `python -m mypy tests alembic` |
| Tests | `python -m pytest -v` |

Tests marked `integration` need Postgres at `DATABASE_URL` (start it with `docker compose up -d`).
They are **not** skipped when the DB is missing; they fail, so a missing DB can't be mistaken for
a pass.

Current tests (M0), 4 total:

| Test | Kind | Covers |
|---|---|---|
| `test_error_body_has_the_documented_shape` | unit | the one error shape |
| `test_health_ok_when_database_is_up` | integration | 200 with a real Postgres |
| `test_health_returns_503_with_error_shape_when_database_is_down` | unit (unreachable DB URL) | 503 + error shape |
| `test_request_id_is_echoed_back` | unit | `X-Request-ID` round trip |

## Web (`apps/web`)

| Check | Command |
|---|---|
| Lint | `npm run lint` |
| Types | `npm run typecheck` (runs `next typegen` first; generated route types are not committed) |
| Unit tests | `npm test` (vitest) |
| Build | `npm run build` |

Current tests (M0): 4 vitest tests for `fetchHealth` (ok, degraded with error shape, degraded
with unexpected body, unreachable).

Not covered yet: the rendered page itself (checked by hand in M0 by loading it with the API up,
the DB down, and the API down). Playwright end-to-end tests arrive in M7.

## Pre-commit

`pre-commit install` (from the API venv) runs hygiene hooks, ruff and eslint on commit. Type
checks and tests run in CI, not in the hook.

## CI

`.github/workflows/ci.yml` runs both jobs on every push and PR. The API job uses a
`pgvector/pgvector:pg16` service container and checks that the `vector` extension is available.
