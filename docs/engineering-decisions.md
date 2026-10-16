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
- Code locations: TBD

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
