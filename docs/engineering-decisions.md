# Engineering decisions

One entry per significant decision. Newest at the bottom. Format:

```
## ED-NNN: <title>  (date, milestone)
- Problem / decision:
- Alternatives considered:
- Chosen:
- Why:
- Trade-offs:
- Code locations:
```

The first entries were made during project selection, before any code existed. Claude Code
should fill in "Code locations" once the code exists.

---

## ED-001: Modular monolith  (2026-10-15, pre-M0)
- Problem / decision: overall shape of the system.
- Alternatives considered: microservices per signal; serverless functions per signal.
- Chosen: one FastAPI codebase with two entrypoints (HTTP API, worker), one Next.js front end.
- Why: one developer, ten weeks; signals are independent modules behind one interface, which
  gives the separation benefits without network boundaries between them.
- Trade-offs: API and worker deploy together; a heavy signal (image embeddings) shares a
  process type with light ones. Revisit if one signal needs separate scaling.
- Code locations: TBD

## ED-002: Postgres-backed job queue instead of Redis/Celery  (2026-10-15, pre-M0)
- Problem / decision: how background signal jobs are queued and executed.
- Alternatives considered: Celery + Redis; RQ; a hosted queue (SQS); the `procrastinate` library.
- Chosen: a `jobs` table claimed with `SELECT ... FOR UPDATE SKIP LOCKED`, with leases,
  retries with exponential backoff, idempotency keys and a dead-letter state.
- Why: one fewer piece of infrastructure (Postgres is already required); enqueueing can happen in
  the same transaction that creates the check, so a check never exists without its jobs; the
  concurrency behaviour is explicit and testable, which is valuable to explain in interviews.
- Trade-offs: lower throughput ceiling than a dedicated broker and polling adds latency; this is
  acceptable at student-tool scale. Must be load tested in M9 and the numbers recorded.
- Code locations: `apps/api/rentalscamchecker/jobs/` (M2; details in ED-017 to ED-019)

## ED-003: Python for the API and ML, TypeScript for the web app  (2026-10-15, pre-M0)
- Problem / decision: languages per component.
- Alternatives considered: full-stack TypeScript (Next.js API routes or NestJS) with an
  ML microservice in Python.
- Chosen: FastAPI (Python 3.12) for API + worker; Next.js (TypeScript) for the UI.
- Why: image hashing, embeddings and evaluation tooling are strongest in Python; keeping all
  backend logic in one language avoids a second service. Next.js matches existing experience.
- Trade-offs: two toolchains; API types must be shared via OpenAPI-generated client types.
- Code locations: TBD

## ED-004: Contacts stored as keyed hashes only  (2026-10-15, pre-M0)
- Problem / decision: how to link listings by phone/email without storing personal data.
- Alternatives considered: store raw values encrypted; store plain SHA-256 hashes.
- Chosen: normalize, then HMAC-SHA256 with a secret pepper from the environment.
- Why: equality matching still works for the graph; plain hashes of phone numbers are trivially
  reversible by brute force (small search space), while HMAC requires the secret.
- Trade-offs: pepper rotation requires rehashing or a dual-key period; raw values can't be shown
  back to users later.
- Code locations: `apps/api/rentalscamchecker/contacts.py`, `checks/service.py` (M1)

## ED-005: Rules first, trained model only with real labels  (2026-10-15, pre-M0)
- Problem / decision: how risk is scored.
- Alternatives considered: train a classifier from day one on synthetic or scraped data.
- Chosen: explainable rule-based scoring (v1); a trained classifier (v2) only once enough real
  labeled outcomes exist, and only if it beats the rule baseline on a held-out set.
- Why: no trustworthy labeled scam dataset exists for Canadian rental listings; synthetic labels
  would produce a model that looks good and means nothing. Explainability matters because the
  product must justify every flag to users and to disputed landlords.
- Trade-offs: v1 has less "ML" on the resume; the honest framing (pretrained models + external
  LLM API + deterministic rules, with real evaluation) is stronger in interviews than a fake one.
- Code locations: TBD

## ED-006: Never output a "scam" verdict  (2026-10-15, pre-M0)
- Problem / decision: user-facing wording.
- Alternatives considered: binary scam / not-scam label; probability score shown to users.
- Chosen: three risk levels plus per-signal evidence and next steps; dispute flow for landlords.
- Why: defamation risk from false positives; evidence-based wording is also more useful.
- Trade-offs: less punchy UI.
- Code locations: TBD

## ED-007: Local Postgres via Docker Compose with pgvector image, non-default ports  (2026-10-16, M0)
- Problem / decision: how to run Postgres 16 + pgvector locally on a Windows machine where Docker
  has been unreliable, and where 5432/5433/3000/8000 are already used by other projects.
- Alternatives considered: the existing native Postgres 16 service (no pgvector; building it on
  Windows needs the VS C++ build tools); a hosted dev DB (Neon); Docker.
- Chosen: `pgvector/pgvector:pg16` via Docker Compose on host port 5434; API on 8100, web on 3100.
  Native and hosted fallbacks documented in docs/deployment.md.
- Why: Docker was verified working in M0; the same image runs in CI, so local and CI match.
- Trade-offs: depends on Docker Desktop (history of crashes on this machine); non-default ports
  must be remembered (all configurable via `.env`).
- Code locations: `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`

## ED-008: /health checks the database and fails with 503 in the standard error shape  (2026-10-16, M0)
- Problem / decision: what "healthy" means, and how failure is reported.
- Alternatives considered: liveness-only `/health` (always 200); separate `/livez` and `/readyz`.
- Chosen: one endpoint that runs `SELECT 1`; 200 `{"status":"ok","database":"ok"}` or 503 with
  `{"error":{"code":"database_unavailable",...}}`. Connect timeout bounded by
  `DATABASE_CONNECT_TIMEOUT` (default 2 s).
- Why: the API can do nothing useful without the DB, so a DB failure is an unhealthy API; using
  the SPEC's one error shape keeps clients simple.
- Trade-offs: a platform using `/health` as a liveness probe would restart the API during a DB
  outage, which doesn't help. Split into liveness/readiness at deploy time (M9) if the host needs it.
- Code locations: `apps/api/rentalscamchecker/main.py`, `apps/api/rentalscamchecker/db.py`

## ED-009: Web reads API health server-side; Next.js 16 Cache Components kept on  (2026-10-16, M0)
- Problem / decision: how the home page calls `/health`, given Next 16 enables Cache Components
  (partial prerendering) by default.
- Alternatives considered: client-side fetch from the browser (needs CORS now); turning Cache
  Components off and using `dynamic = "force-dynamic"` (older model).
- Chosen: an async server component behind `<Suspense>` that calls `await connection()` then
  `fetchHealth()`. The static shell is prerendered; the status streams per request.
- Why: no CORS needed yet; follows the framework's current default rather than opting out;
  `fetchHealth` is a pure function so it is unit-tested without Next.
- Trade-offs: Cache Components is new; `connection()` must be called outside try/catch or the
  prerender bail-out gets swallowed. Documented in the code.
- Code locations: `apps/web/app/page.tsx`, `apps/web/lib/health.ts`

## ED-010: Node 24 as the minimum  (2026-10-16, M0)
- Problem / decision: TASKS.md says Node 20+; vitest 5 requires `@types/node` ≥22.
- Chosen: `engines.node >=24`, `@types/node ^24`, CI on Node 24.
- Why: Node 20 reached end of life in April 2026; the dev machine runs 24.14.1; types should
  match the runtime.
- Trade-offs: none significant for a project that controls its own runtime.
- Code locations: `apps/web/package.json`, `.github/workflows/ci.yml`

## ED-011: M1 schema scope: tables now, columns and indexes when their consumer exists  (2026-10-16, M1)
- Problem / decision: TASKS M1 says "the SPEC data model (except users/outcomes/disputes)", but
  later milestones own pieces of it (M2 the jobs queue, M5 embeddings, M6 graph indexes).
- Alternatives considered: create only the tables M1 itself uses; create everything including
  a guessed `vector(512)` column and all indexes.
- Chosen: create every SPEC table except users/outcomes/disputes in `0002_domain_model`. Defer
  `check_photos.embedding` to M5, because the vector dimension depends on the model M5 picks.
  Defer indexes for queries that don't exist yet (job claim, contact-hash lookup, photo similarity)
  to the milestone that writes the query, where `EXPLAIN` can justify them.
- Why: matches the M1 wording; an index or column added before its query exists is a guess, and
  the M6 task explicitly asks for indexes justified by EXPLAIN.
- Trade-offs: more migrations later (one each in M2, M5, M6), which is normal.
- Code locations: `apps/api/rentalscamchecker/models.py`, `apps/api/alembic/versions/0002_domain_model.py`

## ED-012: TEXT + CHECK constraints instead of Postgres ENUM types  (2026-10-16, M1)
- Problem / decision: how to store statuses, severities, contact kinds, risk levels.
- Alternatives considered: Postgres `CREATE TYPE ... AS ENUM`; lookup tables with FKs.
- Chosen: `TEXT` columns with named `CHECK (col IN (...))` constraints; allowed values defined
  once as tuples in `models.py`.
- Why: adding a value is dropping and re-adding one constraint in a migration, in a transaction.
  `ALTER TYPE ... ADD VALUE` has transaction restrictions, and removing a value is not supported.
  Values are readable in raw SQL without casts.
- Trade-offs: a few bytes more per row than an enum's 4 bytes; nothing enforces that the Python
  tuples and the constraints stay in sync except `alembic check` in CI.
- Code locations: `apps/api/rentalscamchecker/models.py`

## ED-013: Photos are re-encoded to metadata-free JPEG, with limits checked before decoding  (2026-10-16, M1)
- Problem / decision: how to accept untrusted images safely (SPEC 7 and 10).
- Alternatives considered: store the original bytes and strip EXIF with a metadata tool; keep the
  original format (PNG/WebP) when re-encoding.
- Chosen: allow JPEG/PNG/WebP; reject > 10 MB before decoding; read the header and reject either
  side > 8000 px or > 40 MP before decoding pixels; also lower Pillow's own bomb guard
  (`Image.MAX_IMAGE_PIXELS`) to 40 MP and turn its warning into an error. Then apply the EXIF
  rotation, convert to RGB and save as JPEG quality 90 with no EXIF/ICC.
- Why: re-encoding strips metadata (GPS, device) by construction rather than by a list of
  tags to remove; it proves the file decodes; one stored format keeps M5's hashing uniform.
  Checking the header first means a tiny "decompression bomb" PNG never allocates its pixels.
- Trade-offs: re-encoding costs some quality and CPU per photo (not measured yet; the M9
  load test should); transparency is
  flattened; HEIC (iPhone default) isn't supported. Most phones convert to JPEG on upload,
  but this may need a plugin later. Lowering `MAX_IMAGE_PIXELS` is process-global.
- Code locations: `apps/api/rentalscamchecker/images.py`

## ED-014: Contacts hashed during the request; validation reports all errors at once  (2026-10-16, M1)
- Problem / decision: where normalization/hashing happens, and how invalid input is reported.
- Chosen: `POST /checks` normalizes (phone → E.164 via `phonenumbers`, default region CA; email →
  trimmed + lowercased) and HMACs `"<kind>:<value>"` before anything is written. Validation is a
  pure function that checks every field and collects all errors. Photo errors join the same list
  (`photos.N`), returned as one `validation_error`.
- Alternatives considered: hash in a background job (SPEC wording allows it, but the raw value
  would then sit in the `jobs` payload); stop at the first invalid field.
- Why: raw contacts never touch the database or the job queue. Reporting every field lets the
  form show all problems in one round trip. The `kind:` prefix keeps the phone and email
  namespaces apart.
- Trade-offs: an invalid-looking phone number blocks the submission (the user must fix or clear
  it). Lowercasing the email local part is not strictly RFC-correct (see `contacts.py`).
- Code locations: `apps/api/rentalscamchecker/checks/validation.py`, `contacts.py`, `checks/service.py`

## ED-015: CI push trigger limited to main  (2026-10-16, M1)
- Problem / decision: every push to a PR branch ran CI twice (push + pull_request events).
- Chosen: `on.push.branches: [main]`, keep `pull_request`.
- Why: PR branches are covered by `pull_request`; `main` still gets a run after each merge.
- Trade-offs: a branch pushed without a PR gets no CI. That's acceptable with the
  one-PR-per-milestone workflow.
- Code locations: `.github/workflows/ci.yml`

## ED-016: Contacts inside the listing text are extracted, hashed and redacted  (2026-10-16, M2)
- Problem / decision: M1 stored `listing_text` as submitted, so phone numbers and emails written
  in the text (common in real listings) were persisted raw, contradicting the privacy rule. Those
  contacts are also the ones most useful to the contact graph.
- Alternatives considered: (1) accept it until the M8 retention job deletes old text; (2) redact
  only; (3) redact and also link the extracted contacts. Humayun chose (3).
- Chosen: before storing, `extract_contacts()` replaces every email (first) and phone number
  (libphonenumber's `PhoneNumberMatcher`, default region CA) with `[email]` / `[phone]`, and
  returns the normalized values. They are HMAC-hashed into `check_contacts` with
  `source = 'text'`; form-field contacts have `source = 'field'`. Contacts are de-duplicated on
  (kind, normalized value) before insert, field first, so a number in both the field and the text
  is one row with source `field`. The DB enforces it with `UNIQUE (check_id, kind, hmac_hash)`,
  which replaces `UNIQUE (check_id, kind)`. At most 10 distinct values per kind are kept from the
  text, but every occurrence is redacted. Migration `0003_contact_source` applies the same
  extraction to rows stored before it.
- Why: the raw value never exists in the database, logs or job payloads, and the graph gets
  every contact a scammer reused. Emails go first so `4165550123@gmail.com` is one email, not an
  email plus a phone. The matcher only accepts valid numbers, so prices, dates, postal codes and
  unit numbers are left alone (tested).
- Trade-offs: the text signal (M4) sees placeholders rather than numbers, which loses nothing for
  its flag set. Deliberately obfuscated numbers ("four one six…", letter O for zero) are not
  caught. The redaction is irreversible: downgrading 0003 drops the `text` rows for good.
  The migration needs `CONTACT_HASH_PEPPER` at deploy time.
- Code locations: `apps/api/rentalscamchecker/contacts.py` (`extract_contacts`),
  `checks/validation.py` (`_merge_contacts`), `models.py` (`CheckContact.source`),
  `alembic/versions/0003_contact_source.py`

## ED-017: Queue semantics: at-least-once, leases, attempt-number fencing, reaper  (2026-10-16, M2)
- Problem / decision: what guarantees the jobs table gives, and how crashes, retries and stale
  workers are handled.
- Alternatives considered: holding the row lock (open transaction) for the whole job instead of a
  lease; exactly-once by doing the work and the completion in one transaction; jitter in backoff;
  a heartbeat that extends leases for long jobs; the `procrastinate` library.
- Chosen:
  - **Claim**: one `UPDATE ... WHERE id = (SELECT ... ORDER BY run_after, id LIMIT 1 FOR UPDATE
    SKIP LOCKED) RETURNING ...`, committed immediately. It sets `status = 'running'`,
    `attempts + 1` and `locked_until = now() + lease`. After the commit the lease, not a lock,
    reserves the job.
  - **Statuses**: `failed` means "the last attempt failed and the job will be retried at
    `run_after`"; claimable like `queued`. `dead` is the dead-letter state: attempts used up, a
    `PermanentJobError`, or no handler. `last_error` is kept.
  - **Backoff**: `min(max, base * 2^(attempt-1))`, defaults 2 s and 300 s. No jitter.
  - **Reaper**: every poll, `running` jobs past `locked_until` go back to `failed` (due now), or
    to `dead` if that was the last attempt. A crash therefore costs an attempt, so a job that
    kills the worker every time is dead-lettered instead of looping forever.
  - **Fencing**: `complete`/`fail` only apply `WHERE status = 'running' AND attempts = <attempt
    the worker claimed>`. A worker whose lease expired and whose job was re-claimed gets "not
    owned" back and changes nothing.
  - **At-least-once delivery**: a job can run twice (crash after the work but before `complete`;
    a timed-out attempt still running), so handlers must be idempotent. Results are upserted on
    `(check_id, name, version)` with an explicit rule (ED-020).
  - **Per-job timeout** (added before M3): each attempt runs in a helper thread; after
    `JOB_TIMEOUT_SECONDS` (default 45 s) the worker fails it as a retryable `JobTimeout` and
    moves on. The timeout must be below `JOB_LEASE_SECONDS` (default 60 s), checked at startup by
    both `Settings` and `WorkerConfig`, so an overrunning job is always resolved by its own
    worker (failed, retried with backoff) before the lease expires and the reaper hands it out.
- Why: short transactions (claims don't hold connections or locks during slow network calls);
  each failure mode has one explicit, tested rule. Jitter only matters when many clients retry
  against one service at the same moment, which isn't the case here.
- Trade-offs: no heartbeat, so no job may legitimately need more than `JOB_TIMEOUT_SECONDS`.
  The timeout is hard for the **job**, not for the **code**: Python can't kill a thread, so a
  timed-out handler keeps running in an abandoned daemon thread until it returns. It no longer
  owns the job (fencing), its result write is idempotent (ED-020), and the count of
  still-running abandoned threads is logged (`job_timed_out`). But it still uses a thread and a
  DB connection, and a signal that truly hangs forever leaks one thread per attempt. So signals
  must still set their own network timeouts (M3: the geocoder uses 10 s). A real kill would need
  one process per job (multiprocessing), which is much heavier, especially on Windows; revisit if
  abandoned threads show up in practice. The reaper runs on every poll of every thread, which is
  cheap thanks to its partial index but redundant; fine at this scale.
- Code locations: `apps/api/rentalscamchecker/jobs/queue.py`, `jobs/worker.py`

## ED-018: Aggregator enqueued at the check timeout, started early when every signal is done  (2026-10-16, M2)
- Problem / decision: how the aggregator knows to run "when all signal jobs are terminal or a
  check-level timeout passes" (TASKS M2).
- Alternatives considered: an aggregator job that polls (re-enqueues itself every few seconds
  until done); a scheduler process that scans for finished checks; counting job rows by a
  `check_id` column on `jobs`.
- Chosen: `POST /checks` enqueues, in the same transaction as the check, one `signal` job per
  enabled signal and one `aggregate` job due at `now() + CHECK_TIMEOUT_SECONDS`. That job's payload
  lists the expected (signal, version) pairs. Whenever a signal result is written (success, or the
  error result written when a signal job is dead-lettered), the same transaction locks the check
  row (`SELECT ... FOR UPDATE`), compares recorded results with the expected list and, if all are
  present, sets the aggregate job's `run_after = now()`. The aggregator locks the check, writes an
  error result ("did not finish before the check timeout") for each missing signal, and marks the
  check `complete`, with `risk_level = 'incomplete'` if any expected signal errored. If the
  aggregate job itself is dead-lettered, the check is closed as `incomplete` so the UI doesn't poll
  forever.
- Why: no polling and no extra process; the timeout is just the job's due time. Locking the
  check row serializes concurrent signal completions for the same check, so two signals finishing
  at once can't both miss each other's result (without the lock the aggregator would still run,
  but only at the timeout).
- Trade-offs: the check row lock adds a little contention per check (signals of one check are few).
  A signal finishing after the timeout is ignored for that report: its insert hits the existing
  error row. `risk_level` stays NULL on a complete check until scoring exists (M7). Checks created
  before M2 have no jobs and stay `pending`.
- Code locations: `apps/api/rentalscamchecker/pipeline.py`, `checks/api.py`, `checks/service.py`

## ED-019: Worker = threads in one process; graceful stop on SIGTERM/SIGINT/SIGBREAK  (2026-10-16, M2)
- Problem / decision: how the worker runs jobs concurrently and shuts down.
- Alternatives considered: asyncio (all code is sync SQLAlchemy and sync signal libraries);
  one process per job slot (multiprocessing); a pool of processes.
- Chosen: `python -m rentalscamchecker.worker` starts `WORKER_CONCURRENCY` threads, each looping
  reap -> claim -> run -> complete/fail, sleeping `WORKER_POLL_INTERVAL_SECONDS` when the queue is
  empty. SIGTERM, SIGINT and (Windows) SIGBREAK set a stop event: running jobs finish, no new job
  is claimed, the process exits 0. The main thread joins with a timeout so it keeps handling
  signals. Every log line during a job carries `job_id` and `job_kind` (structlog contextvars are
  per thread).
- Why: the work is I/O-bound (DB, network APIs), where threads are enough despite the GIL; the
  CPU-heavy image embedding in M5 may need processes. Scale out with more worker processes;
  SKIP LOCKED makes that safe (tested with 2 workers x 4 threads).
- Trade-offs: polling adds up to one poll interval of latency (LISTEN/NOTIFY could remove it).
  On Windows, SIGTERM can't be delivered to another process (it becomes TerminateProcess), so the
  graceful path is tested there with CTRL_BREAK; production (Linux) uses SIGTERM, also tested in CI.
- Code locations: `apps/api/rentalscamchecker/worker.py`, `jobs/worker.py`

## ED-020: Signal results: upsert with "a result beats an error, the final report is frozen"  (2026-10-16, pre-M3)
- Problem / decision: with at-least-once delivery and the per-job timeout (ED-017), the same
  signal can write its result more than once, sometimes concurrently and sometimes after the
  check is final. Which write wins?
- Context: `UNIQUE (check_id, name, version)` has existed since M1 (`uq_signal_results_check_id_name_version`),
  and M2 inserted with `ON CONFLICT DO NOTHING` (first write wins), so duplicates were already
  impossible. What was missing was a deliberate rule for *which* write wins.
- Alternatives considered: first write wins (M2); last write wins (plain upsert), which could
  change a report the user has already seen, e.g. a late success overwriting the aggregator's
  "did not finish" error after the check was marked complete.
- Chosen: `_record_result` locks the check row, then:
  - check `complete` (or deleted): write nothing; the report is final.
  - no row: insert.
  - existing row is `error` and the new result is not: replace it
    (`ON CONFLICT ... DO UPDATE ... WHERE signal_results.status = 'error' AND excluded.status <> 'error'`).
  - otherwise keep the existing row: the first real result wins, and an error never replaces a result.
- Why: a successful retry or a late success is better information than "could not be
  completed"; once the aggregator has finalized the check, nothing may change underneath it.
  One row per key is guaranteed by the constraint either way.
- Trade-offs: two different successful results for the same key (non-deterministic signal run
  twice) keep the first, not the latest. Taking the check row lock on every result write
  serializes writes per check; checks have few signals.
- Code locations: `apps/api/rentalscamchecker/pipeline.py` (`_record_result`)

## ED-021: price_vs_market baseline: CMHC vacant-unit rent first, by municipality, with fallbacks  (2026-10-16, M3)
- Problem / decision: which CMHC figure a listing's rent is compared with, and how the data gets
  into the database. Answers SPEC open question "Can CMHC zone boundaries be obtained?".
- Findings:
  - CMHC publishes Rental Market Survey (RMS) averages by bedroom type (studio, 1, 2, 3+) per CMA,
    per survey zone, and per municipality, each with a reliability letter (a-d) or suppressed (`**`).
    The survey covers the purpose-built rental market (row and apartment units), not condos,
    basements or rooms. Latest: October 2025.
  - **The overall average understates what a new tenant pays.** It is dominated by long-tenure
    sitting tenants. Humayun raised this before the dataset was committed. CMHC's old "Average
    Apartment Rents (Vacant & Occupied Units)" product stopped at the 2020 edition, but the current
    RMS data-table workbooks still publish the split: table 3.1.9 "Average Rents ($) of Vacant and
    Occupied Units by Zone and Bedroom Type" (row and apartment). Its rows include whole
    municipalities (e.g. "Mississauga City (Zones 18-20)", "Zone 23 - Oakville") and the CMA. Every
    row we use carries CMHC's "Y" flag: vacant rents are statistically higher than occupied rents
    (95% confidence).
  - October 2025, 2-bedroom, row and apartment (vacant units from table 3.1.9; all units from
    table 3.1.2, identical to the HMIP portal figures):

    | | vacant units | all units | vacant vs all |
    |---|---|---|---|
    | Oakville | $2,621 (b) | $2,309 (b) | +13.5% |
    | Mississauga | $2,473 (a) | $2,001 (a) | +23.6% |
    | Brampton | $2,331 (a) | $2,025 (a) | +15.1% |
    | Toronto CMA | $2,530 (a) | $2,045 (a) | +23.7% |

    For comparison, the RMS report's Toronto 2-bedroom "turnover rent" (rent on a new lease; Rental
    Market Report Fall 2025, p. 87) was
    $2,547 in 2025, close to the vacant figure. Against the all-units baseline, a listing 30%
    below would be 38-43% below the vacant baseline in these cities, so the old baseline would miss
    exactly the listings this signal exists for.
  - Coverage: vacant values are suppressed more often (32 of 120 values suppressed overall), but
    every municipality in our list still gets a vacant baseline for every bedroom bucket, its own or
    its CMA's (table in the M3 log). The vacant data is therefore usable, not too suppressed.
- Alternatives considered: overall (all-units) average only, as first implemented; turnover rent
  (published only per CMA and mostly for 2-bedroom in the report text, not as a per-municipality
  table); zone level with boundary files (Mississauga and Brampton have 2-3 sub-zones; would need
  point-in-polygon on a geocode, making the signal depend on the geocoder).
- Chosen:
  - Baseline order (revised before M4, see "Correction" below); a figure counts only with
    reliability a, b or c:
    1. municipality, vacant units;
    2. **estimated municipality vacant** = municipality all-units × (CMA vacant / CMA all-units,
       same bedroom bucket), when all three are usable. `rent_basis =
       municipality_estimated_vacant`; evidence carries `estimate_ratio` and the three inputs; the
       explanation says "estimated from local averages" and gives the ratio. Its reliability is
       the worst of the three inputs';
    3. CMA, vacant units;
    4. CMA, all units.
    Otherwise `skipped`. Reliability `d` is never used.
  - Municipality level matched by the city the user typed (normalized: `City of Oakville, ON` →
    `oakville`, Toronto districts → `toronto`), with no network, so the signal is independent of
    the geocoder. address_sanity separately checks that the typed city matches the geocoded one.
  - `unit_type` on the submission (`whole_unit` default, `room`, `basement`). Rooms are `skipped`
    ("CMHC's rent data covers whole units only"). Basements are compared, with a caveat that they
    aren't in the survey and often rent for less, and their severity is **capped at `low`**
    (pre-M4).
  - Severity: `medium` from 30% below the baseline, `high` from 50% below (`PRICE_*_BELOW_PERCENT`);
    never flagged when above.
  - Evidence: listing and reference rent, percent below, `rent_basis` (`vacant_units`/`all_units`),
    a plain-language `baseline` ("average asking rent for vacant 2-bedroom units in Oakville"),
    level, the CMHC row used (`cmhc_geography`), CMA, survey, reliability, source URL and CMHC's
    required "Adapted from ..." attribution. Thresholds are deliberately left out (SPEC 10).
  - Data: `data/reference/cmhc/fetch_rms.py` reads CMHC's official workbooks (Toronto and Hamilton
    CMAs) and writes the CSV with provenance on every row (licence approved by Humayun; see
    `data/reference/cmhc/README.md`). The loader `python -m rentalscamchecker.reference_rents <csv>`
    upserts it. The loader lives in the app package so it is type-checked and tested.
- Why: the vacant-unit average is CMHC's published figure closest to the price a new tenant is
  asked, which is what a listing shows; municipality figures match how users describe a listing.
  Answer to the SPEC question: zone boundaries aren't needed for v1.
- Correction (pre-M4): **the first order (M3) caused false positives.** It was municipality
  vacant → CMA vacant → municipality all-units → CMA all-units. Where a municipality's vacant value
  was suppressed, the CMA's vacant figure won over the municipality's own data even when the CMA
  is much pricier. Milton/Halton Hills 2-bedroom was compared with $2,530 (Toronto CMA vacant)
  while the zone's own all-units average is $1,667, so a typical $1,700 Milton 2-bed showed as 33%
  below and was flagged `medium`. Markham was affected the same way. Real 2-bedroom baselines
  after the change (October 2025):

  | | before | after | how |
  |---|---|---|---|
  | Milton | $2,530 (CMA vacant) | $2,062 (b) | estimated: 1,667 × (2,530 / 2,045 = 1.2372) |
  | Markham | $2,530 (CMA vacant) | $2,469 (c) | estimated: 1,996 × 1.2372 |
  | Caledon | $2,530 (CMA vacant) | $2,530 (a) | unchanged: Caledon's all-units value is suppressed too |

  A $1,700 Milton 2-bed is now 17.6% below the baseline: severity `none`. A regression test runs
  this case against the committed dataset (`test_typical_milton_two_bedroom_is_not_flagged`).
- Trade-offs:
  - The estimate assumes a municipality's vacant/all-units ratio equals its CMA's. That's an
    approximation: across the 3 municipalities in the comparison table the ratio ranges
    1.135-1.236 (Toronto CMA: 1.237). It
    uses local price levels, which matter more than the exact premium.
  - Purpose-built rentals only: basements and condos can legitimately differ (caveat in the text;
    to be measured in the evaluation). A misspelled or unlisted city means no comparison. The
    dataset must be refreshed yearly.
- Code locations: `apps/api/rentalscamchecker/reference_rents.py`, `signals/price_vs_market.py`,
  `checks/validation.py` (`unit_type`), `data/reference/cmhc/fetch_rms.py`, migrations
  `0005_reference_rents_geocoding` and `0006_check_unit_type`

## ED-022: Nominatim client: identified, cached, globally rate-limited through Postgres  (2026-10-16, M3)
- Problem / decision: how address_sanity geocodes while following the Nominatim usage policy
  (max 1 request/s in total, identifying User-Agent, results cached, no repeated queries,
  attribution, ODbL data).
- Alternatives considered: a per-process `time.sleep` limiter (doesn't hold across several worker
  processes); Redis (new infrastructure); a paid geocoder (cost; SPEC says later if needed).
- Chosen:
  - User-Agent `RentalScamChecker/0.1 (+<repo URL>; <NOMINATIM_CONTACT_EMAIL>)`. The email comes
    only from the environment; the API and the worker refuse to start if `address_sanity` is
    enabled without a valid one.
  - Cache table `geocode_cache` keyed by the normalized query; "not found" is cached too.
  - Rate limit: a `rate_limits` row per service. A caller locks it (`SELECT ... FOR UPDATE`, with a
    `GEOCODER_LOCK_WAIT_SECONDS` (15 s) `lock_timeout`), sleeps until `NOMINATIM_MIN_INTERVAL_SECONDS` (≥ 1, validated) has
    passed since the last request, makes the HTTP call **while holding the lock**, then records the
    time. All our requests are serialized across every thread and process.
  - Query: `"<address>, <city>, Canada"`, `countrycodes=ca`, `format=jsonv2`, `addressdetails=1`,
    `limit=1`; HTTP timeouts 5 s connect and 10 s per read. HTTP errors and network errors raise `GeocodingUnavailable`, and
    the job is retried with backoff. Errors are not cached.
  - Attribution "© OpenStreetMap contributors (data under the ODbL)" is in every address_sanity
    result's evidence for the UI to show.
- Rules (address_sanity): not found → `low`; found in a different place than the typed city →
  `medium` (the typed city may match any locality field: city, town, suburb, neighbourhood..., so
  "Streetsville" matches a Mississauga address); OSM class/type is non-residential (amenity, shop,
  office, commercial or school building, ...) → `medium`; both → `high`.
- Why: Postgres is already there and already coordinates the workers (ED-002); the limiter is
  correct across processes and is tested with three geocoders racing.
- Trade-offs: holding the row lock during the HTTP call means queued geocodes wait in line, so
  each costs about 1 s plus latency; fine at our volume. Many concurrent checks would back up
  (after the 15 s lock wait the job fails and retries). The geocoded point isn't yet written to the
  `checks.geocoded_*` columns; it's in the result evidence, and the M6 graph will decide where it
  lives. OSM's coverage of house numbers in new subdivisions is incomplete, which is why not-found
  is only `low`.
- Code locations: `apps/api/rentalscamchecker/geocoding.py`, `signals/address_sanity.py`,
  `signals/registry.py`, migration `0005_reference_rents_geocoding`

## ED-023: Abandoned threads: the slot is freed, each external call has its own timeout budget, and workers report counters  (2026-10-16, M3)
- Problem / decision: after a per-job timeout (ED-017), what still holds resources, how to stop
  external calls from outliving the job, and how operators can see it.
- Findings (tested): a timed-out attempt does **not** keep its worker slot. The worker thread stops
  waiting at the timeout, records a retryable failure and claims the next job. With one slot and a
  job that overruns for 3 s, the next job finished within 2.5 s while the first one's helper thread
  was still running (`test_worker_keeps_processing_new_jobs_while_a_timed_out_one_is_still_running`).
  What stays busy is the **helper thread**, plus any DB connection it holds, until its code returns.
- Chosen:
  - **Timeout budget per external call**, validated at startup in `Settings`: every client's worst
    case must be below `JOB_TIMEOUT_SECONDS`, so a slow service fails the attempt by itself instead
    of leaving an abandoned thread. Nominatim: lock wait 15 + rate-limit interval 1 + connect 5 +
    read 10 = 31 s < 45 s. Anthropic (M4): cap lock wait 5 + timeout 25 × (retries 0 + 1) = 30 s
    < 45 s; the SDK's retries multiply its timeout, so they are off (the job queue retries).
    Caveat: httpx timeouts apply per phase (connect, and each read or write), not to the whole
    request, so a server trickling bytes could in theory exceed the sum; the job timeout remains
    the backstop.
  - **Counters**: each worker counts jobs done, failed and timed out, and abandoned helper threads
    still alive. `job_timed_out` log lines carry `abandoned_threads_alive` and
    `jobs_timed_out_total`. Every `WORKER_HEARTBEAT_INTERVAL_SECONDS` (5 s) each worker process
    upserts its row in `worker_heartbeats`, and marks `stopped_at` on a clean exit.
  - **`GET /internal/metrics`** (API) returns jobs by status, workers alive (heartbeat < 30 s old
    and not stopped), total abandoned threads alive, timeouts, and per-worker rows. Access: with
    `INTERNAL_METRICS_TOKEN` set, a matching `Authorization: Bearer` header is required (constant
    time compare); without it, the endpoint is open in development/test and returns 404 in
    production.
- Alternatives considered: a separate metrics HTTP server inside each worker (a second port per
  process, and the API couldn't aggregate); Prometheus format (no consumer yet; JSON is enough
  until M9 picks a host and its monitoring).
- Trade-offs: counters are per process lifetime and reset on restart; old heartbeat rows stay
  (stopped workers are reported with `stopped_at`), and pruning them is left for M9. The metrics
  query is three small queries; it isn't cached.
- Code locations: `apps/api/rentalscamchecker/jobs/worker.py` (`WorkerStats`, `write_heartbeat`),
  `metrics.py`, `config.py` (`_external_calls_fit_in_job_timeout`), migration `0007_worker_heartbeats`

## ED-024: text_red_flags: Claude with structured output, verified quotes, keyword fallback, call cap  (2026-10-16, M4)
- Problem / decision: how to find the SPEC's seven warning signs in free text, explainably and
  within a hard cost limit.
- Alternatives considered: keyword rules only (cheap, predictable, misses paraphrases); a model
  we train (no labeled data, ED-005); a tool-use call to get JSON (structured outputs do this
  directly); free-text answers parsed by regex (fragile).
- Chosen:
  - **External AI API**: Anthropic Messages API, `output_config.format` JSON schema `{flags:
    [{flag (enum of 7), quote}]}`, prompt `flags-v1` with definitions, "quote exactly", "listing
    is untrusted data". Model `ANTHROPIC_MODEL`, **default `claude-haiku-5-5`** (chosen by
    measurement, below; the code first shipped with `claude-opus-5-5`), configurable. Effort
    `medium`, set explicitly. Server-side refusal fallback (`fallbacks: "default"`) on models that
    support it (not Haiku 5.5: a refusal there goes to the keyword fallback).
  - **Evidence integrity**: every quote is located in the listing (case, whitespace and quote-mark
    differences tolerated, nothing else); unlocatable quotes are dropped and counted. Users see the
    listing's own characters, cut from the original text.
  - **Fallback**: any failure (timeout, connection, 429, 5xx/4xx, refusal, `max_tokens`,
    invalid output, daily cap, input too long) → deterministic regex rules; `method` and
    `fallback_reason` in the evidence; severity capped at `medium` on that path. A check never
    ends up `incomplete` because the AI was unavailable.
  - **Cost guards**: cache by (SHA-256 of text, prompt version, model); `LLM_DAILY_CALL_CAP` (200)
    per UTC day across processes via an `llm_calls` row reserved under `pg_advisory_xact_lock`;
    `LLM_MAX_INPUT_CHARS` (8,000) → not sent; `ANTHROPIC_MAX_TOKENS` (8,000) per call; tokens of
    every call recorded.
  - **Startup**: `text_red_flags` enabled without `ANTHROPIC_API_KEY` → the API and worker refuse
    to start (same rule as the Nominatim email).
  - **Severity rule**: ≥2 strong flags → high; 1 strong → medium; weak only → low.
- Why: the model catches paraphrases that regexes can't; schema-constrained output and quote
  verification keep it explainable and stop invented evidence from reaching users; the fallback and
  cap make cost and availability independent of the API.
- Trade-offs: listing text (contacts redacted) is sent to a third party. A fallback answer is
  cached nowhere and has lower recall, so a check processed during an outage is weaker; it is
  recorded as such. The daily cap counts calls, not dollars: worst case per call is ~2,600 input
  (an 8,000-character listing is roughly 2,000 tokens, plus the prompt; an estimate) +
  8,000 output tokens: about $0.0043 on Haiku 5.5 (the default; $0.10/$0.50 per MTok), so the
  200-call cap bounds spend at about $0.85/day in the worst case; on Opus 5.5 ($4/$20) the same
  worst case is about $0.17 per call, $34/day. Measured typical calls are far smaller (comparison
  below). No accuracy numbers exist until the hand-labeled set does.
- Model choice (Humayun's rule: the cheaper model unless the expensive one is clearly better):

  Measured 2026-10-16 (30 API calls in total, all `ok`): the LLM eval on the 12-example synthetic
  fixture plus 3 live checks through the API and worker, per model, prompt `flags-v1`, effort `medium`.
  Latency, tokens and cost come from `llm_calls`; cost is an estimate at list prices
  (Opus 5.5 $4/$20, Haiku 5.5 $0.10/$0.50 per MTok).

  | | claude-haiku-5-5 | claude-opus-5-5 |
  |---|---|---|
  | Synthetic eval, micro precision / recall (18 labels, 12 examples) | 1.00 / 0.83 (15 TP, 0 FP, 3 FN) | 1.00 / 0.94 (17 TP, 0 FP, 1 FN) |
  | Misses | refuses_viewing in "my agent can't show it either"; an implied deposit (syn-07); syn-03 deposit | syn-03 deposit (a debatable label) |
  | Invented/unverifiable quotes dropped | 0 | 0 |
  | Fallbacks (API unavailable) | 0 | 0 |
  | Live checks (3): flags found | identical to Opus on all 3 | identical to Haiku on all 3 (slightly tighter quotes) |
  | Latency per call, eval: median / max | 1,254 / 3,988 ms | 2,716 / 4,803 ms |
  | Latency per call, live: median / max | 1,595 / 1,649 ms | 2,892 / 4,984 ms |
  | Tokens per call, input median | 1,118 (eval), 1,135 (live) | same input |
  | Tokens per call, output median / max | 80 / 126 (eval), 119 / 180 (live) | 124 / 404 (eval), 163 / 420 (live) |
  | Estimated cost per check | $0.00015 (eval), $0.00017 (live) | $0.0075 (eval), $0.0085 (live) |

  Per flag, synthetic set (support / Haiku recall / Opus recall; precision 1.00 everywhere):
  deposit_before_viewing 5 / 0.60 / 0.80; owner_unavailable_abroad 3 / 1.00 / 1.00; keys_by_mail
  2 / 1.00 / 1.00; off_platform_payment 3 / 1.00 / 1.00; refuses_viewing 2 / 0.50 / 1.00;
  urgency_pressure 2 / 1.00 / 1.00; too_good_to_be_true_language 1 / 1.00 / 1.00.

  **The set is synthetic, tiny (12 examples, 18 labels) and written by the same author as the
  labels; it shows the pipeline works, not how accurate either model is on real listings.**

  Decision: **claude-haiku-5-5**. Opus caught 2 more of 18 labels on the synthetic set (both on
  implied signs) and found the same flags on all 3 live checks, at ~50x the cost and ~2x the
  latency. That is a hint, not clear evidence. Re-run the comparison on the real labeled set
  (docs/labeling-guide.md) before trusting either number, and switch with `ANTHROPIC_MODEL` if
  Opus's recall advantage holds on real data.
- Code locations: `apps/api/rentalscamchecker/text_flags/` (flags, prompt, llm, spans, keywords),
  `signals/text_red_flags.py`, `signals/registry.py`, `config.py`, migration `0008_llm_calls`,
  `eval/text_flags.py`

## ED-025: Photo reuse: perceptual hashes + CLIP ViT-B/32 (int8 ONNX on CPU), calibrated on CC0 fixtures  (2026-10-17, M5)
- Problem / decision: detect a listing's photos being reused from an earlier check at another
  address, robust to the edits re-posters make, on a CPU-only machine; choose the embedding model,
  the runtime, the thresholds and the search method; and keep the paid web search off.
- **Runtime and model** (Humayun asked for a CPU-friendly option over a full PyTorch install if
  quality is comparable):
  - Chosen: OpenAI's CLIP ViT-B/32 image encoder (MIT licence) as ONNX, run with `onnxruntime`;
    preprocessing reimplemented with Pillow + numpy (no `transformers`). A **pretrained model we
    run**; we didn't train it.
  - Measured install, before installing: new Python packages 69 MB download / ~204 MB unpacked
    (onnxruntime 14.9 MB, scipy 36.7 MB, numpy 12.6 MB, PyWavelets, ImageHash, pgvector, protobuf,
    flatbuffers). The PyTorch route needs a 124 MB `torch` wheel on its own (installed size not
    measured). ONNX runs the same weights, so quality is the same model's by construction; the
    open question was fp32 vs int8.
  - fp32 (Qdrant export, MIT-tagged, 352 MB) vs int8 (Xenova export, 89 MB; no licence tag of its
    own, derived from the same MIT weights), on this machine (AMD, 12 logical CPUs, Windows 11):
    per-image median 86-95 ms fp32 vs 100-103 ms int8 (two runs; int8 is not faster on this CPU);
    model load 1.8-2.7 s fp32 vs 0.6-1.0 s int8; recall at the precision-1.0 cosine threshold
    identical (0.545 at 0.94 for both); hardest non-duplicate 0.921 (fp32) vs 0.928 (int8).
    **Chosen: int8** (`PHOTO_EMBEDDING_MODEL=clip-vit-b32-int8`): same quality on the fixtures,
    a quarter of the download and disk, faster start. Files pinned to a repository revision and
    verified by SHA-256 (`photos/model_files.py`), stored in `var/models/` (gitignored).
- **Hashes**: pHash and dHash (`imagehash`, 64 bits each), stored as signed BIGINT.
- **Thresholds**, calibrated with `eval/photo_dupes.py` on 24 CC0/public-domain Wikimedia Commons
  interiors (manifest with source and licence per image, 4.35 MB, committed) × 12 edits = 288
  duplicate pairs, 6,900 non-duplicate pairs including 525 hard negatives (similar-looking but
  different rooms). Report with all curve points: `eval/reports/photo_dupes.md`. Key points:
  - pHash alone reaches precision 1.0 up to t = 18 (recall 0.646), first false positive at 20;
    dHash alone up to t = 14 (recall 0.590).
  - CLIP alone: precision 1.0 from cosine 0.94 (recall 0.545); the most similar different room
    scored 0.928.
  - Combined rule (chosen): **single hash ≤ 10, OR both hashes ≤ 18, OR cosine ≥ 0.95** →
    recall 0.774 (223/288), 0 false positives, 0 hard-negative hits. Neighbouring points:
    both-hashes ≤ 20 → 0.799 with 0 FP; ≤ 22 → first 2 FP; cosine ≥ 0.93 → 0.833 with 0 FP but
    only 0.002 above the hardest negative.
  - Why not the higher-recall points: a query photo is compared with every stored photo, so the
    per-pair false-match rate must stay tiny as the database grows. For random 64-bit hashes
    P(d ≤ 18) ≈ 3×10⁻⁴ (about 2 false hits per query at 10,000 photos) but P(d ≤ 10) ≈ 10⁻⁸;
    requiring BOTH hashes for the looser 18-bit allowance and keeping 0.02 cosine margin above the
    hardest negative trades fixture recall for safety at scale.
  - Recall by edit at the chosen point: resize, recompression, colour, greyscale, mirror (via the
    embedding), watermark 1.00; rotation 0.92; screenshot border 0.83; crop+resize+recompress 0.75;
    centre crop 80% 0.58; corner crop 70% 0.17; tight crop 55% 0.04. **Known gap: crops removing
    ≥30% of the picture.** Their hashes change completely and their embeddings fall into the range
    of different-but-similar rooms. Options if this matters in practice: store embeddings of
    several crops per photo, or add local-feature matching (e.g. ORB + RANSAC) as a second stage.
- **Hash search**: Postgres scan with `bit_count((phash # :q)::bit(64))`, no index. Measured
  (`eval/reports/hash_search_bench.md`): 7 ms at 10k photos, 47 ms at 100k, vs an in-process
  BK-tree at 21-24 ms and 224-257 ms (it visits ~100% of nodes at radius 18, so its pruning doesn't
  help, and every worker process would have to rebuild it). Revisit beyond ~1M photos.
- **Embedding search**: pgvector HNSW index (`vector_cosine_ops`), top 20 neighbours per photo,
  then the threshold.
- **Signal rules** (`photo_reuse_internal`): matches at the same street address (normalised:
  abbreviations, units) are information, not a warning; other addresses or a previously
  high-risk check → `high` for a hash match, `medium` for an embedding-only match. Fingerprints
  are computed once and stored on `check_photos`.
- **photo_reuse_web (TinEye)**: behind `TINEYE_ENABLED=false`; cache by image SHA-256; daily cap
  (`TINEYE_DAILY_SEARCH_CAP`, reserved under an advisory lock); at most 2 photos per check; budget
  2 × 15 s + 5 s < 45 s job timeout. **The adapter has never called TinEye**: no account exists,
  per Humayun. The response shape follows TinEye's published client libraries; the request
  convention (`POST {api}/search/`, field `image_upload`, header `x-api-key`) could not be checked
  against TinEye's reference (tineye.com didn't resolve from the dev machine). Verify before
  enabling. Tests use fakes and a mock transport only.
- Trade-offs: CPU embedding adds ~0.1 s per photo (cold start ~4.7 s for the first check in a
  worker process, measured); the model adds 89 MB to every worker deployment; crops are the known
  weakness above; same-address detection is a string heuristic, not geocoding.
- Code locations: `apps/api/rentalscamchecker/photos/` (hashing, embeddings, matching,
  model_files, web_search), `signals/photo_reuse_internal.py`, `signals/photo_reuse_web.py`,
  `addresses.py`, migration `0009_photo_signals`, `eval/photo_dupes.py`,
  `eval/hash_search_bench.py`, `eval/fixtures/photos/`
