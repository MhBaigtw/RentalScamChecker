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
- Code locations: TBD

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
