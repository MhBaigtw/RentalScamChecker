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
