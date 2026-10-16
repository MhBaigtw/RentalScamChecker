# TASKS.md — milestones

Work top to bottom. Tick a milestone only when every "Done when" item is verified by actually
running it. Record what was verified (commands + key output) in the milestone's log line.

Target pace: about 20 hours/week, 10 weeks. Weeks are a guide, not a contract.

---

## M0 — Foundation (week 1)

- [x] Verify environment: Python 3.12 via `py`, Node 20+ (now 24+, ED-010), Git, free disk space; Docker works?
      If not, set up the fallback in CLAUDE.md and document it in docs/deployment.md.
- [x] Monorepo layout per CLAUDE.md; root README; `.gitignore` (incl. `eval/private/`, `.env`).
- [x] Postgres 16 + pgvector available locally (docker compose or fallback).
- [x] `apps/api`: FastAPI skeleton, settings via env, structlog JSON logging, `GET /health`
      that checks the DB connection; Alembic initialized with an empty baseline migration.
- [x] `apps/web`: Next.js + TypeScript strict + Tailwind; home page calls `/health`.
- [x] Tooling: ruff, mypy, pytest (api); eslint, tsc, vitest (web); pre-commit hooks.
- [x] GitHub Actions CI: lint + type check + tests for both apps, Postgres service container.
- [x] docs/ seeded per docs/README.md.

Done when: `GET /health` returns ok with DB up and fails cleanly with DB down; CI is green on a
pushed branch; a fresh clone can be set up by following the README.

## M1 — Domain model and submission API (week 2)

- [x] SQLAlchemy models + Alembic migration for the SPEC data model (except users/outcomes/disputes).
- [x] Storage interface + local-disk implementation; images re-encoded, EXIF stripped, size and
      dimension limits, decompression-bomb protection.
- [x] `POST /checks` (multipart) with validation and one error shape; `GET /checks/{id}`.
- [x] Contact normalization (phonenumbers lib, email lowercasing) + HMAC hashing with pepper.
- [x] Unit tests for validation and normalization; integration tests for both endpoints.

Done when: a check with 3 photos can be created and read back; invalid input returns the
documented error shape; raw contact values are not in the database (verified by query).

## M2 — Job queue and worker (weeks 2–3)

- [ ] `jobs` table and repository: enqueue (idempotent on key), claim with
      `FOR UPDATE SKIP LOCKED`, lease/visibility timeout, complete, fail with backoff, dead-letter.
- [ ] Worker entrypoint with graceful shutdown (SIGTERM finishes current job) and concurrency setting.
- [ ] Orchestration: `POST /checks` enqueues one job per enabled signal; aggregator job runs when
      all signal jobs are terminal or a check-level timeout passes.
- [ ] A stub signal that sleeps and returns a fixed result, to exercise the pipeline.
- [ ] Tests: two workers never process the same job; a crashed job is re-claimed after its lease
      expires; retries back off; poison job goes to dead-letter.

Done when: those concurrency tests pass against real Postgres, and a check created via the API
reaches "complete" through the worker with the stub signal.

## M3 — price_vs_market (week 3)

- [ ] Download CMHC average rent table (record source URL, survey year, licence in docs);
      loader into `reference_rents`; investigate zone boundaries (answer SPEC open question).
- [ ] Geocoding client (Nominatim) with caching table, rate limiting (max 1 req/s), proper
      User-Agent; `address_sanity` signal.
- [ ] `price_vs_market` signal with configurable threshold; clear explanation text.
- [ ] Unit tests with fixtures; no live network in tests (record/replay or fakes).

Done when: both signals run in the pipeline for a real Oakville/Mississauga address and the
report shows evidence (reference rent, year, CMA, percentage below).

## M4 — text_red_flags (week 4)

- [ ] Prompt + JSON schema for the flag set; structured output via the Anthropic API; model name
      configurable; prompt version stored with every result; `llm_cache` by content hash.
- [ ] Each flag returns the quoted evidence span; spans validated against the source text.
- [ ] Deterministic keyword fallback when the API errors or times out; result marks which path ran.
- [ ] Cost guard: max input length, per-day call cap.
- [ ] `eval/text_flags.py`: runs the signal over `eval/private/text_labels.jsonl`, prints per-flag
      precision/recall with the set size. (Humayun builds the labeled set; until it exists, the
      harness runs on a tiny synthetic fixture and says so.)

Done when: the signal runs in the pipeline; eval harness produces a report; fallback path is
covered by a test that simulates an API failure.

## M5 — Photo signals (weeks 5–6)

- [ ] Perceptual hashing (imagehash: pHash, dHash) stored as 64-bit ints; Hamming-distance search
      (Postgres `bit_count` on XOR, or an in-process BK-tree if faster; measure and record).
- [ ] Image embeddings from a pretrained model on CPU (choose and justify in engineering-decisions);
      pgvector index; similarity threshold calibrated on the fixture set.
- [ ] `photo_reuse_internal` signal combining both, with evidence (matched check ids, distance,
      differing city/address).
- [ ] `photo_reuse_web` TinEye adapter behind feature flag (OFF), spend cap, caching; tests with fakes.
- [ ] `eval/photo_dupes.py`: fixture set of transformed images + non-duplicates; report
      precision/recall and per-image latency measured on Humayun's machine.

Done when: re-submitting a listing with cropped/resized versions of earlier photos is flagged;
eval report exists with real measured numbers.

## M6 — Contact graph (week 6)

- [ ] `contact_reuse` signal: links via HMAC hashes; evidence = count of other checks, distinct
      cities/addresses, prior risk levels.
- [ ] Graph queries for "related checks" (photos OR contacts), cluster size; indexes justified
      with EXPLAIN output recorded in docs/database-design.md.

Done when: three seeded checks sharing a phone number and a photo are linked in each other's reports.

## M7 — Scoring and report UI (week 7)

- [ ] Versioned scoring config; aggregator computes level + "Incomplete" handling; stores version.
- [ ] Wording rules enforced (no "scam" verdicts) with a test that scans user-facing strings.
- [ ] Web: submission form (photo upload with previews), progress view, report page with each
      signal's evidence and next step, mobile-friendly, accessible (labels, contrast, keyboard).

Done when: full journey works in the browser end to end; Playwright e2e test covers submit → report.

## M8 — Accounts, outcomes, disputes, abuse controls (week 8)

- [ ] Auth (decide provider; record decision); history page; outcome prompt and endpoint.
- [ ] Dispute form + admin-only review list (simple, protected).
- [ ] Rate limiting per IP and per contact hash; request size limits; CORS locked down.
- [ ] Retention job: deletes raw text/photos past the retention window; test it.

Done when: outcome and dispute flows work end to end; rate limit returns 429 with the error shape.

## M9 — Hardening and deploy (week 9)

- [ ] Security pass: dependency audit, secrets scan, input fuzzing on the upload endpoint.
- [ ] Load test the worker (e.g. 200 queued checks) and record real throughput/latency numbers.
- [ ] Choose hosting; present Humayun with options and monthly cost; deploy only after approval.
- [ ] Production config, migrations on deploy, health checks, log shipping, error tracking.

Done when: deployed URL works end to end; deployment steps reproducible from docs/deployment.md.

## M10 — Documentation complete, then teaching (week 10)

- [ ] All docs/ files complete and consistent with the code.
- [ ] README with architecture diagram, screenshots, setup, and honest limitations.
- [ ] docs/interview-preparation.md and docs/learning-guide.md drafted from the actual code.

After M10, the build is handed back to Humayun for Phase B (tutor mode and mock interviews)
outside Claude Code.

---

## Log

(Claude Code: append one line per milestone: date, milestone, what was verified, commit.)
- 2026-10-16, M0: env checked (py -3.12 → 3.12.6, Node 24.14.1, Git 2.55.0, 25.9 GB free, Docker 29.2.1 OK);
  `docker compose up --wait` → Postgres 16.15 + pgvector 0.8.7 (CREATE EXTENSION ok); live API on :8100:
  `/health` 200 `{"status":"ok","database":"ok"}` with DB up, 503 `database_unavailable` error shape
  with DB stopped (2.0 s, bounded by connect timeout), 200 again after restart without restarting the API;
  web on :3100 shows ok / degraded / unreachable for DB up / DB down / API down; api: ruff ok, mypy strict ok,
  pytest 4/4; web: eslint ok, tsc ok, vitest 4/4, build ok (/ is partial prerender); CI run 38851272104
  green (both jobs) on branch m0-foundation; fresh clone of m0-foundation set up per README (short path,
  see P-004): pytest 4/4, npm ci, lint, typecheck, vitest 4/4. Commits 10d9fc6 (main), e582964 + follow-up (m0-foundation).
- 2026-10-16, M1: migration 0002 upgrade → downgrade → upgrade OK, `alembic check` no drift; api: ruff ok,
  mypy strict ok (app + tests/alembic), pytest 83/83 (5 integration, real Postgres 16.15 in Docker);
  live API on :8100: `POST /checks` with 3 photos (JPEG with EXIF/GPS, PNG RGBA, WebP) → 202, `GET` → 200 with
  3 photos 640x480/800x600/400x300 and contacts [phone, email]; stored files are JPEG with 0 EXIF tags;
  invalid form → 422 `validation_error` listing 6 fields; unknown id → 404 `not_found`; psql: both
  `hmac_hash` 32 bytes, 0 rows in checks/check_contacts/check_photos/signal_results containing the raw
  phone or email. CI run 38968326188 green (both jobs; one pull_request run, no duplicate push run;
  83 passed, `alembic check` no drift). CI fix (push trigger → main only) is its own commit aff0c3c;
  M1 commits d148ee8 (code) + 3c396aa (docs) on branch m1-domain-model, PR #2.
