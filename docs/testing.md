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

Current tests (M7): 402 total, all passing locally on 2026-10-18 (Windows); 139 of them are
`integration` (real Postgres; the 3 timeout-config cases in `test_job_queue.py` don't need the DB
but inherit the module's marker). **No test touches the network**: Nominatim is replaced by
`tests/nominatim_fake.py` (an httpx `MockTransport` with hand-written `jsonv2`-shaped responses),
the photo tests use the committed CC0/public-domain fixture images and the real int8 CLIP model
(fetched in CI, cached), CMHC data by `tests/fixtures/rms_synthetic.csv`, whose values are synthetic and labelled so in
every row, and the Anthropic API by `tests/anthropic_fake.py` (canned responses, the SDK's real
exception types). The one test that reads the committed real CMHC dataset is a regression test
(`test_typical_milton_two_bedroom_is_not_flagged`). The full suite takes about 45 s, mostly real waits for leases,
backoffs and the worker process.

| File | Kind | Covers |
|---|---|---|
| `test_health.py` (4) | unit + 1 integration | error shape, `/health` up/down, request id echo |
| `test_contacts.py` (27) | unit | phone formats → one E.164 value, foreign numbers, invalid phones/emails, email lowercasing, HMAC deterministic / depends on pepper / depends on kind; extraction from text: common formats, no false positives on prices/dates/postal codes/units, digits inside an email, repeats listed once but all redacted, 10-per-kind cap |
| `test_validation.py` (35) | unit | `unit_type` defaults to `whole_unit`, accepts room/basement, rejects anything else; cleaned output, all missing fields reported at once, rent formats and bad rents (incl. NaN/Infinity, 3 decimals), bedrooms, URL schemes, length limits, per-field contact errors; text contacts redacted; same number in field and text → one contact (`field`) |
| `test_jobs_unit.py` (14) | unit | geocoder worst case (lock wait + interval + connect + read = 31 s) must fit inside the 45 s job timeout, and a config that doesn't is rejected; job timeout must be below the lease (Settings) and the defaults are 45 s / 60 s; backoff doubling and cap; enabled-signal parsing, empty list, unknown name fails at startup; stub signal's fixed result |
| `test_job_queue.py` (17) | integration | **with one worker slot, a job that overruns its timeout doesn't block the next job; abandoned-thread and timeout counters**; **a job exceeding the per-job timeout is failed (`JobTimeout`, worker returns after ~0.5 s, not 2 s) and retried; the abandoned first attempt finishing later changes nothing**; timeout ≥ lease or 0 rejected; idempotent enqueue; `run_after` respected; **a row being claimed is skipped, not waited for** (two open transactions, `lock_timeout` 2 s); **2 workers × 4 threads process 200 jobs: each exactly once**; **crashed job re-claimed after its 1 s lease expires**, and the crashed worker's late complete/fail are rejected (fencing); crash on the last attempt → dead via the reaper; **backoff: run_after − updated_at is exactly 1 s, then 0.25/0.5/1 s doubling**, job not claimable before; **poison job → dead after max_attempts** with last_error and one on_dead call; permanent error → dead after 1 attempt; unknown kind → dead; in-process stop lets the running job finish and claims no more |
| `test_pipeline.py` (9) | integration | **upsert rule (ED-020): a success replaces an earlier error result; an error never replaces a result; a signal that times out and then finishes late in its abandoned thread still leaves exactly one row**; aggregator starts as soon as the last signal is done (an hour before its timeout); dead signal → error result + check `incomplete`; **check timeout finalizes without the missing signal**, and its late result is ignored; running a signal job twice stores one result; no signals → complete immediately; **end to end: POST /checks → jobs committed with ids-only payloads → worker → GET shows `complete` with the stub result** |
| `test_worker_process.py` (1) | integration | the real `python -m rentalscamchecker.worker` process: stop signal sent while the stub job runs (SIGTERM on Linux; CTRL_BREAK on Windows, where SIGTERM can't be delivered) → job finishes `done`, the aggregator it made due stays `queued`, exit code 0, log order `shutdown_requested` < `job_done`, last line `worker_stopped` |
| `test_reference_rents.py` (35) | unit + integration | city normalization (incl. names ending in "on": P-008); bedroom buckets; CSV parsing and validation; reload is an upsert; **fallback order: municipality vacant → CMA vacant (beats municipality all-units) → municipality all-units → CMA all-units**, `d` and suppressed values skipped, newest survey wins, CMHC row recorded; nothing usable → None |
| `test_price_vs_market.py` (18) | integration | severity at/around 30% and 50% below the **vacant-unit** baseline; **evidence has reference rent, survey year, CMA, percent below and which baseline was used (`rent_basis`, `baseline`)**; CMA and all-units fallbacks named; **rooms skipped (whole units only)**; basements compared with a caveat; unknown city skipped; thresholds never shown; no "scam" wording |
| `test_geocoding.py` (9) | integration | request params and identifying User-Agent with the contact email (not the httpx default); cache hit across instances; not-found cached; **three geocoders racing are spaced ≥ 0.95 s apart** (1 req/s across processes); HTTP 429/500/503 and connection errors → retryable, nothing cached |
| `test_address_sanity.py` (10) | unit (fake geocoder) | found + right city → none; not found → low; other city → medium; non-residential (school, shop, commercial, office) → medium; both → high; typed neighbourhood matches suburb; street-only match noted; attribution in evidence |
| `test_m3_signals_pipeline.py` (7) | unit + 1 integration | API and worker refuse to start with address_sanity but no/blank/invalid `NOMINATIM_CONTACT_EMAIL`; price_vs_market alone needs none; **POST /checks → worker → both signals → GET shows complete with price evidence (reference rent, year, CMA, % below) and the address match** |
| `test_metrics.py` (3) | unit + 1 integration | `/internal/metrics` needs the bearer token when set; 404 in production without one; reports jobs by status and a worker's heartbeat (alive, then stopped) |
| `test_text_flags_unit.py` (38) | unit | quote location (case/whitespace/curly quotes tolerated; paraphrases and tiny quotes rejected); every keyword rule fires, ordinary listings (Interac e-Transfer, viewings offered, house rules) don't; severity rule; signal with a stub extractor: verified quotes, invented quote dropped and counted, duplicate flag once, **every API failure reason falls back to keyword rules, says so, capped at medium**; schema enumerates the 7 flags |
| `test_text_flags_llm.py` (17) | integration | request uses structured output + explicit effort + server-side refusal fallback (plain endpoint for models without it); cache hit makes no call; cache is per model; **daily cap stops calls, and holds with 8 concurrent callers against a cap of 3**; long input not sent; timeout / connection / 429 / 500 / refusal / max_tokens / malformed output each map to a reason, logged in `llm_calls`, never cached |
| `test_text_flags_pipeline.py` (8) | unit + integration | API refuses to start without `ANTHROPIC_API_KEY`; Anthropic worst case must fit the job timeout; **full journey on the AI path (redacted text sent, no phone number)**; **full journey with a simulated API failure (timeout, 503) → keyword fallback, check completes** |
| `test_eval_text_flags.py` (10) | unit | eval harness: per-flag counts, precision/recall, n/a when never predicted, report states set size and the synthetic warning; **label-file validation reports every problem by line number** (bad JSON, not an object, missing/unknown keys, duplicate id, unknown or repeated flags, wrong types, empty file); cost estimate from list prices |
| `test_photos_unit.py` (20) | unit + real model | signed-hash Hamming; resized copy hashes close, different room far; match-rule boundaries; address normalisation (incl. `#B` units, found a regex bug); **real int8 CLIP**: 512-d normalised vectors, mirrored copy ≥ 0.95, different room < 0.95; missing model is a clear error |
| `test_photo_signals.py` (15) | integration + real model | first check stores fingerprints; **resized copy at another address → high (hash)**; mirrored copy → medium (embedding only); same-address repost → information; **similar but different rooms don't match**; re-run reuses fingerprints; web search OFF → skipped with no call; fake web matches → medium, per-check limit, cache; cap → skipped; **TinEye request shape and parsing via a mock transport (never the real API)**; startup checks (model missing, TinEye key missing, TinEye budget); **end to end via API + worker: cropped/resized re-submission flagged high**; explanation grammar |
| `test_check_graph.py` (16) | integration + real model | **M6 Done-when via API + worker: three checks sharing a phone (typed two ways in the form, only in the listing text of the third) and a photo (resized / re-encoded) list each other in `related`, cluster size 3, no other check's id anywhere in the reports**; **two checks sharing only a photo at the same address → photo signal none, contact signal skipped, related shows it as same address**; `related` null until complete; cluster follows phone→photo→email chains; cap; related limit vs total; photo links stored both ways and idempotently; **embedding-only matches link too**; deleting a check removes its links; contact_reuse: never seen / same address only → none, same city → low, other city → medium, prior high-risk → high, no contacts → skipped, text-sourced contacts link like form fields |
| `test_scoring.py` (32) | unit + integration | levels from points; address_sanity alone never high; skipped adds nothing; errors -> incomplete unless high; optional signal errors ignored; **graph-dependent high alone capped at some; lifts own some to high; independent level ignores graph results**; contributing names only; config validation (10 invalid configs); **(a) poisoning: fake high-risk listing with a landlord's phone -> landlord's next clean listing some, not high; (b) no chain A -> B -> C via phone, nor via a same-address photo link; (c) linked check with its own photo-reuse evidence -> contact_reuse high, naming it** (real contact_reuse + real aggregator) |
| `test_wording.py` (11) | unit | every string literal in the API package (docstrings excluded) free of verdict phrasing and of "scam" as a word; the checker catches known verdicts and allows the brand name; labels are exactly the four levels; every summary clean |
| `test_cors.py` (2) | unit | web origin allowed for POST, no credentials; other origins get no allow-origin header |
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
- (M5) cosine threshold loosened to 0.80: `test_similar_but_different_rooms_do_not_match` fails; `same_address` always False: `test_same_address_repost_is_information_not_a_warning` fails.
- (M6) photo branch removed from the related-checks query, or from the cluster search; other-city
  rule removed from contact_reuse; `public_ref` returning the raw id: each makes
  `test_check_graph.py` fail (4 of 4 caught).
- (M7) contact_reuse reads `risk_level` instead of `independent_risk_level`; scorer without the
  one-step cap; independent level counting graph-dependent results; photo signal treating an
  independent "some" as high; photo signal never setting `depends_on_other_checks`: each
  makes `test_scoring.py` or `test_photo_signals.py` fail (5 of 5). Without the cap, the
  poisoning and legitimate-case tests both fail on their own. A verdict inserted into a
  signal explanation makes `test_wording.py` fail.
- (M4) daily-cap advisory lock removed: `test_daily_cap_holds_under_concurrency` fails (3 of 3 runs).
- (M3) worker waits for the helper thread instead of timing out (`helper.join()`): `test_worker_keeps_processing_new_jobs_while_a_timed_out_one_is_still_running` fails.

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

| End-to-end | `npm run e2e` (Playwright, Chromium; starts everything itself, see below) |
| Screenshots | `npm run e2e:screenshots` (writes docs/screenshots/, on request only) |

Unit tests (M7): 17 vitest tests: `fetchHealth` (4); `lib/api.ts` (5: created, field errors with
per-photo errors folded, unreachable, not found incl. invalid id, ok); `lib/signals.ts` (6:
finding labels, evidence formatting, nested rows, per-signal labels, level names, a title for
every signal); wording (2: the checker itself, and no verdict phrasing in any page, component or
library file).

End-to-end (M7, ED-028), 4 tests in `e2e/journey.spec.ts`, against the real web app (`next build`
+ `next start` on 3110), API (8110), worker and Postgres. `apps/api/tests/e2e_server.py` creates
and empties a separate database (`<dev db>_e2e_test`), loads the CMHC rents and enables only
signals without network calls (price, both photo signals, contact; TinEye off):
1. **submit -> progress -> report**: a $900 2-bedroom in Toronto with 2 photos and a phone ->
   "High risk signals" based on the price; cards for price, photos and contact; price card shows
   "Strong risk signal", a next step, and evidence ($900) after "Show the evidence"; web search
   "Not checked"; no verdict wording in the page text;
2. a second listing reusing the photo and phone in another city -> photo card strong signal and a
   "Linked listings" section naming the first (same phone, same photo);
3. submitting an empty form -> error summary takes focus and links to the photo field;
4. keyboard: skip link first, then the fields in order.
Run time on the dev machine: 52 s including the Next.js build. Also in CI (job `e2e`).

Not covered: other browsers (Chromium only), an automated accessibility audit (axe) and contrast
measurement, the text and address signals in the browser (they need network; checked once by
hand against the dev stack in M7).

## Pre-commit

`pre-commit install` (from the API venv) runs hygiene hooks, ruff (check + format) on `apps/api/`
and `eval/`, mypy (the same two runs as CI, through `scripts/precommit-mypy.sh` and the API venv,
when any Python file there changes; ~9 s warm, ~40 s cold) and eslint on commit. Tests run in CI,
not in the hook. Until M7 `eval/` was only linted in CI, which let a broken string literal reach a
commit in M6.

## CI

`.github/workflows/ci.yml` runs both jobs on every pull request and on pushes to `main` (ED-015). The API job uses a
`pgvector/pgvector:pg16` service container and checks that the `vector` extension is available, runs `alembic upgrade head`, then `alembic check`
(fails if models and migrations have drifted).
