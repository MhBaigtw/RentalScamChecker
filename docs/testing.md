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

Integration tests write to the database they run against and don't clean up (rows are scoped
by fresh UUIDs, so tests don't interfere with each other). Locally that is the dev database. Photo
files go to a per-test `tmp_path`.

Current tests (M1): 83 total, all passing locally on 2026-10-16; 5 of them are `integration`
(real Postgres).

| File | Kind | Covers |
|---|---|---|
| `test_health.py` (4) | unit + 1 integration | error shape, `/health` up/down, request id echo |
| `test_contacts.py` (22) | unit | phone formats → one E.164 value, foreign numbers, invalid phones/emails, email lowercasing, HMAC deterministic / depends on pepper / depends on kind |
| `test_validation.py` (27) | unit | cleaned output, all missing fields reported at once, rent formats and bad rents (incl. NaN/Infinity, 3 decimals), bedrooms, URL schemes, length limits, per-field contact errors |
| `test_images.py` (13) | unit | EXIF (incl. GPS) removed, EXIF orientation applied, PNG/WebP → JPEG, GIF / non-image / truncated / empty / >10 MB / >8000 px rejected, 49 MP decompression bomb (<100 KB file) rejected |
| `test_storage.py` (8) | unit | put/get/delete, idempotent delete, path-traversal keys refused |
| `test_checks_api.py` (9) | 5 without DB + 4 integration | empty form lists every error; 11 photos; bad photo reported by index and no file written; bad UUID and unknown route use the error shape; **3-photo check created and read back** with stored files verified EXIF-free; **raw contacts absent from every row of the check** and stored hashes equal the expected HMACs; contacts optional; unknown id → 404 shape |

The raw-contact test was checked to be able to fail: a throwaway copy with the phone number
placed in `listing_text` failed as expected (see docs/database-design.md "Known gap").

Not covered: concurrent submissions; storage write failure part-way (cleanup path is code-reviewed,
not tested); real phone photos (HEIC, large JPEGs). Integration tests don't run on a clean
database, so they can't assert global row counts.

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

`.github/workflows/ci.yml` runs both jobs on every pull request and on pushes to `main` (ED-015). The API job uses a
`pgvector/pgvector:pg16` service container and checks that the `vector` extension is available, runs `alembic upgrade head`, then `alembic check`
(fails if models and migrations have drifted).
