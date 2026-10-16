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

Tests marked `integration` need the Postgres server from `DATABASE_URL` (start it with
`docker compose up -d`). They are **not** skipped when the DB is missing; they fail, so a missing
DB can't be mistaken for a pass.

### The test database (dev data and test data never mix)

Integration tests never touch the dev database. They use a separate database on the same server
(`tests/db.py`, fixtures in `tests/conftest.py`):

- Name: `TEST_DATABASE_URL` if set, otherwise `DATABASE_URL` with `_test` appended to the database
  name (locally `rentalscamchecker_test`). Anything whose name doesn't end in `_test` is refused
  before any connection is made (`test_db_guard.py`).
- Once per test session (`test_database` fixture): `CREATE DATABASE` if missing (needs a role
  allowed to create databases, which `rsc` is in Docker and in CI), `alembic upgrade head` run from
  code, then `TRUNCATE` of every table in case an earlier run was interrupted.
- After **every** test marked `integration` (autouse fixture): `TRUNCATE ... RESTART IDENTITY
  CASCADE` on every model table. Tests that aren't marked `integration` never trigger any of
  this, so the unit suite runs without a database.
- Photo files go to a per-test `tmp_path`.

Verified on 2026-10-16: before a run, the server had only `rentalscamchecker`, with 13 checks. After
it, `rentalscamchecker_test` existed at revision `0003_contact_source` with 0 checks, and the dev
database still had the same 13. A second run against the existing test database also passed.

Current tests (M3): 209 total, all passing locally on 2026-10-16 (Windows); 68 of them are
`integration` (real Postgres; the 3 timeout-config cases in `test_job_queue.py` don't need the DB
but inherit the module's marker). **No test touches the network**: Nominatim is replaced by
`tests/nominatim_fake.py` (an httpx `MockTransport` with hand-written `jsonv2`-shaped responses),
and CMHC data by `tests/fixtures/rms_synthetic.csv`, whose values are synthetic and labelled so in
every row. The full suite takes about 45 s, mostly real waits for leases,
backoffs and the worker process.

| File | Kind | Covers |
|---|---|---|
| `test_health.py` (4) | unit + 1 integration | error shape, `/health` up/down, request id echo |
| `test_contacts.py` (27) | unit | phone formats → one E.164 value, foreign numbers, invalid phones/emails, email lowercasing, HMAC deterministic / depends on pepper / depends on kind; extraction from text: common formats, no false positives on prices/dates/postal codes/units, digits inside an email, repeats listed once but all redacted, 10-per-kind cap |
| `test_validation.py` (30) | unit | cleaned output, all missing fields reported at once, rent formats and bad rents (incl. NaN/Infinity, 3 decimals), bedrooms, URL schemes, length limits, per-field contact errors; text contacts redacted; same number in field and text → one contact (`field`) |
| `test_jobs_unit.py` (12) | unit | job timeout must be below the lease (Settings) and the defaults are 45 s / 60 s; backoff doubling and cap; enabled-signal parsing, empty list, unknown name fails at startup; stub signal's fixed result |
| `test_job_queue.py` (16) | integration | **a job exceeding the per-job timeout is failed (`JobTimeout`, worker returns after ~0.5 s, not 2 s) and retried; the abandoned first attempt finishing later changes nothing**; timeout ≥ lease or 0 rejected; idempotent enqueue; `run_after` respected; **a row being claimed is skipped, not waited for** (two open transactions, `lock_timeout` 2 s); **2 workers × 4 threads process 200 jobs: each exactly once**; **crashed job re-claimed after its 1 s lease expires**, and the crashed worker's late complete/fail are rejected (fencing); crash on the last attempt → dead via the reaper; **backoff: run_after − updated_at is exactly 1 s, then 0.25/0.5/1 s doubling**, job not claimable before; **poison job → dead after max_attempts** with last_error and one on_dead call; permanent error → dead after 1 attempt; unknown kind → dead; in-process stop lets the running job finish and claims no more |
| `test_pipeline.py` (9) | integration | **upsert rule (ED-020): a success replaces an earlier error result; an error never replaces a result; a signal that times out and then finishes late in its abandoned thread still leaves exactly one row**; aggregator starts as soon as the last signal is done (an hour before its timeout); dead signal → error result + check `incomplete`; **check timeout finalizes without the missing signal**, and its late result is ignored; running a signal job twice stores one result; no signals → complete immediately; **end to end: POST /checks → jobs committed with ids-only payloads → worker → GET shows `complete` with the stub result** |
| `test_worker_process.py` (1) | integration | the real `python -m rentalscamchecker.worker` process: stop signal sent while the stub job runs (SIGTERM on Linux; CTRL_BREAK on Windows, where SIGTERM can't be delivered) → job finishes `done`, the aggregator it made due stays `queued`, exit code 0, log order `shutdown_requested` < `job_done`, last line `worker_stopped` |
| `test_reference_rents.py` (32) | unit + integration | city normalization (incl. names ending in "on": P-008); bedroom buckets; CSV parsing and validation; reload is an upsert; lookup: newest survey wins, municipality first, suppressed or `d` → CMA fallback, nothing usable → None |
| `test_price_vs_market.py` (15) | integration | severity at/around 30% and 50% below, above-reference wording; **evidence has reference rent, survey year, CMA, percent below**, CMHC attribution; CMA fallback named in the text; unknown city → skipped; thresholds never in user-facing output; no "scam" wording |
| `test_geocoding.py` (9) | integration | request params and identifying User-Agent with the contact email (not the httpx default); cache hit across instances; not-found cached; **three geocoders racing are spaced ≥ 0.95 s apart** (1 req/s across processes); HTTP 429/500/503 and connection errors → retryable, nothing cached |
| `test_address_sanity.py` (10) | unit (fake geocoder) | found + right city → none; not found → low; other city → medium; non-residential (school, shop, commercial, office) → medium; both → high; typed neighbourhood matches suburb; street-only match noted; attribution in evidence |
| `test_m3_signals_pipeline.py` (7) | unit + 1 integration | API and worker refuse to start with address_sanity but no/blank/invalid `NOMINATIM_CONTACT_EMAIL`; price_vs_market alone needs none; **POST /checks → worker → both signals → GET shows complete with price evidence (reference rent, year, CMA, % below) and the address match** |
| `test_db_guard.py` (6) | unit | test-DB name guard refuses non-`_test` names (incl. an injection attempt); default and overridden test-DB URL |
| `test_images.py` (13) | unit | EXIF (incl. GPS) removed, EXIF orientation applied, PNG/WebP → JPEG, GIF / non-image / truncated / empty / >10 MB / >8000 px rejected, 49 MP decompression bomb (<100 KB file) rejected |
| `test_storage.py` (8) | unit | put/get/delete, idempotent delete, path-traversal keys refused |
| `test_checks_api.py` (10) | 5 without DB + 5 integration | empty form lists every error; 11 photos; bad photo reported by index and no file written; bad UUID and unknown route use the error shape; **3-photo check created and read back** with stored files verified EXIF-free; **raw contacts (fields and inside the text) absent from every row of the check**, with the expected HMACs and sources; **same number in field and text → one link**; contacts optional; unknown id → 404 shape |

**Mutation checks (M2).** Each time the code was broken on purpose and the named test was run;
it failed each time, then passed again once the code was restored:
- `FOR UPDATE SKIP LOCKED` → `FOR UPDATE`: `test_a_row_being_claimed_is_skipped_not_waited_for`
  fails (lock timeout).
- fencing removed from complete/fail: `test_crashed_job_is_reclaimed_after_its_lease_expires`
  fails.
- reaper never dead-letters: `test_job_crashing_on_its_last_attempt_is_dead_lettered` fails.
- aggregator never pulled forward: `test_aggregator_starts_as_soon_as_all_signals_are_done` fails.
- SIGBREAK handler removed: `test_stop_signal_lets_the_running_job_finish` fails on Windows.
- (pre-M3) handler called directly instead of under the timeout: `test_job_exceeding_the_timeout_is_failed_and_retried` fails.
- (pre-M3) upsert without the "only replace an error" condition: `test_error_never_replaces_a_result` fails.
- (pre-M3) no "check complete → write nothing" guard: `test_check_timeout_finalizes_without_the_missing_signal` fails.
- (M3) geocoder rate-limit sleep removed: `test_requests_are_spaced_by_the_minimum_interval_across_instances` fails.

The raw-contact test was checked to be able to fail: a throwaway copy with the phone number
placed in `listing_text` failed as expected (see docs/database-design.md "Known gap").

Not covered: concurrent submissions; storage write failure part-way (cleanup path is code-reviewed,
not tested); real phone photos (HEIC, large JPEGs); a DB outage while the worker runs (the loop
catches and logs it, not tested); throughput under load (M9). Timing-based queue tests (leases,
backoff) use real sleeps of 0.25 to 1.5 s with margins; they haven't been flaky in the runs so
far, but they depend on the machine not stalling for more than the margin.

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
