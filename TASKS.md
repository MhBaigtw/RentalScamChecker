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

- [x] `jobs` table and repository: enqueue (idempotent on key), claim with
      `FOR UPDATE SKIP LOCKED`, lease/visibility timeout, complete, fail with backoff, dead-letter.
- [x] Worker entrypoint with graceful shutdown (SIGTERM finishes current job) and concurrency setting.
- [x] Orchestration: `POST /checks` enqueues one job per enabled signal; aggregator job runs when
      all signal jobs are terminal or a check-level timeout passes.
- [x] A stub signal that sleeps and returns a fixed result, to exercise the pipeline.
- [x] Tests: two workers never process the same job; a crashed job is re-claimed after its lease
      expires; retries back off; poison job goes to dead-letter.

Done when: those concurrency tests pass against real Postgres, and a check created via the API
reaches "complete" through the worker with the stub signal.

## M3 — price_vs_market (week 3)

- [x] Download CMHC average rent table (record source URL, survey year, licence in docs);
      loader into `reference_rents`; investigate zone boundaries (answer SPEC open question).
- [x] Geocoding client (Nominatim) with caching table, rate limiting (max 1 req/s), proper
      User-Agent; `address_sanity` signal.
- [x] `price_vs_market` signal with configurable threshold; clear explanation text.
- [x] Unit tests with fixtures; no live network in tests (record/replay or fakes).

Done when: both signals run in the pipeline for a real Oakville/Mississauga address and the
report shows evidence (reference rent, year, CMA, percentage below).

## M4 — text_red_flags (week 4)

- [x] Prompt + JSON schema for the flag set; structured output via the Anthropic API; model name
      configurable; prompt version stored with every result; `llm_cache` by content hash.
- [x] Each flag returns the quoted evidence span; spans validated against the source text.
- [x] Deterministic keyword fallback when the API errors or times out; result marks which path ran.
- [x] Cost guard: max input length, per-day call cap.
- [x] `eval/text_flags.py`: runs the signal over `eval/private/text_labels.jsonl`, prints per-flag
      precision/recall with the set size. (Humayun builds the labeled set; until it exists, the
      harness runs on a tiny synthetic fixture and says so.)

Done when: the signal runs in the pipeline; eval harness produces a report; fallback path is
covered by a test that simulates an API failure.

## M5 — Photo signals (weeks 5–6)

- [x] Perceptual hashing (imagehash: pHash, dHash) stored as 64-bit ints; Hamming-distance search
      (Postgres `bit_count` on XOR, or an in-process BK-tree if faster; measure and record).
- [x] Image embeddings from a pretrained model on CPU (choose and justify in engineering-decisions);
      pgvector index; similarity threshold calibrated on the fixture set.
- [x] `photo_reuse_internal` signal combining both, with evidence (matched check ids, distance,
      differing city/address).
- [x] `photo_reuse_web` TinEye adapter behind feature flag (OFF), spend cap, caching; tests with fakes.
- [x] `eval/photo_dupes.py`: fixture set of transformed images + non-duplicates; report
      precision/recall and per-image latency measured on Humayun's machine.

Done when: re-submitting a listing with cropped/resized versions of earlier photos is flagged;
eval report exists with real measured numbers.

## M6 — Contact graph (week 6)

- [x] `contact_reuse` signal: links via HMAC hashes; evidence = count of other checks, distinct
      cities/addresses, prior risk levels.
- [x] Graph queries for "related checks" (photos OR contacts), cluster size; indexes justified
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
- [ ] Production build fetches the CLIP model file and verifies its SHA-256 (as CI does:
      `python -m rentalscamchecker.photos.model_files clip-vit-b32-int8`); fail the deploy on a
      mismatch. See docs/deployment.md.

Done when: deployed URL works end to end; deployment steps reproducible from docs/deployment.md.

## M10 — Documentation complete, then teaching (week 10)

- [ ] All docs/ files complete and consistent with the code.
- [ ] README with architecture diagram, screenshots, setup, and honest limitations.
- [ ] docs/interview-preparation.md and docs/learning-guide.md drafted from the actual code.

After M10, the build is handed back to Humayun for Phase B (tutor mode and mock interviews)
outside Claude Code.

## Backlog (not scheduled; do not start without Humayun's go-ahead)

- [ ] Crop-robust photo matching: lower the embedding threshold to generate candidates, then verify
      with ORB keypoints + RANSAC inlier count (OpenCV); re-run eval/photo_dupes.py and report
      recall per crop type.
- [ ] Verify the TinEye request format against the live API before enabling it.

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
- 2026-10-16, M2 (branch m2-job-queue, PR #3). Before M2: contacts in listing_text extracted/hashed/redacted
  (fdd851d, ED-016; psql: 0 rows in checks/check_contacts/check_photos/signal_results/jobs contain the raw
  phone or email placed in the text; field+text duplicate → 1 row) and a separate auto-created test DB
  (ab64916; dev DB had the same 13 checks before and after a test run, test DB empty after it).
  M2 (3caeba7 + docs 6b0cb8e + mypy fix): migrations 0003/0004 round-trip, `alembic check` no drift; ruff ok, mypy
  strict ok (also `--platform linux`); pytest 127/127 locally (25 integration) and in CI run 38974250488
  (both jobs green; worker-process test via SIGTERM on Linux, CTRL_BREAK on Windows). Done-when tests pass on
  real Postgres: 2 workers × 4 threads ran 200 jobs exactly once each; crashed job re-claimed after its 1 s
  lease, late complete/fail rejected; backoff 0.25/0.5/1 s exact; poison job dead after 3 attempts.
  Mutation checks (SKIP LOCKED, fencing, reaper, pull-forward, SIGBREAK) each made their test fail. Live API +
  worker on :8100: POST 202 → pending 0.16 s → running 0.66 s → complete 1.72 s with the stub result; aggregate
  job pulled forward from 120 s to 1.78 s. EXPLAIN (202k jobs): claim 0.45 ms via ix_jobs_claimable vs 77 ms
  seq scan.
- 2026-10-16, M3 (branch m3-price-and-address). Pre-M3: deleted the 13 pre-M2 pending dev checks (+31 photo,
  23 contact rows, 2 photo folders); per-job timeout 45 s < 60 s lease (ED-017) and result upsert rule (ED-020);
  API_URL → 127.0.0.1 (P-006 measured: localhost 2,061-2,075 ms vs 12-21 ms). M3: CMHC dataset (licence approved)
  from the official Toronto/Hamilton RMS workbooks, tables 3.1.9 vacant / 3.1.2 all units, Oct 2025, 120 rows;
  2-bed vacant vs all: Oakville 2,621 (b) vs 2,309 (b), Mississauga 2,473 (a) vs 2,001 (a), Brampton 2,331 (a)
  vs 2,025 (a), Toronto CMA 2,530 (a) vs 2,045 (a). SPEC zone question: municipality level suffices (ED-021).
  Migrations 0005-0007 round-trip, `alembic check` clean; ruff, mypy strict (+ --platform linux) clean;
  pytest 226/226 locally (76 integration, no live network). Live API + worker (ENABLED_SIGNALS=price_vs_market,
  address_sanity, real Nominatim, real CMHC data): A 1430 Trafalgar Rd, Oakville, 2-bed $1,500 → complete in
  1.4 s; price medium, 42.8% below vacant 2-bed $2,621 (Oct 2025, CMA Toronto, Zone 23 - Oakville, b); address
  medium: Sheridan College Trafalgar Campus, amenity:college (non-residential). B 3880 Duke of York Blvd,
  Mississauga, 1-bed $1,950 → price none, 4.1% below $2,033 (a); address none (residential, Mississauga).
  C same address, room $750 → price skipped (whole units only); address served from cache. Nominatim: 2 requests,
  1 cache hit; contact email not in logs. /internal/metrics: 1 worker alive, 0 timeouts, 0 abandoned threads,
  jobs_done_total 9 after the next heartbeat.
- 2026-10-16, M4 (branch m4-text-red-flags). Pre-M4: estimated municipal vacant baseline (Milton 2-bed $2,530 → $2,062;
  a $1,700 Milton 2-bed now 17.6% below, severity none; regression test on the real dataset), real signals by
  default, basement severity capped at low. M4: text_red_flags (Anthropic Messages API, structured output, prompt
  flags-v1, quotes verified against the listing, keyword fallback, cache, daily cap 200 via advisory lock, 25 s
  single attempt; startup budget 30 s < 45 s job timeout). Migration 0008 round-trips; ruff, mypy strict (+ linux)
  clean; pytest 304/304 locally (101 integration; no network: fake Anthropic client). Live, 30 API calls total, all ok
  (est. $0.118): synthetic LLM eval (12 examples, 18 labels; SYNTHETIC, author-written) Haiku 5.5 P 1.00 / R 0.83,
  Opus 5.5 P 1.00 / R 0.94, 0 invented quotes dropped, 0 fallbacks; 3 live checks per model through API + worker
  found identical flags (L1 high: abroad/deposit/keys; L2 none; L3 high: deposit/Bitcoin/no viewings/urgency); latency
  median Haiku 1,254-1,595 ms vs Opus 2,716-2,892 ms; est. cost per check Haiku $0.00015-0.00017 vs Opus
  $0.0075-0.0085 → default claude-haiku-5-5 (ED-024). Fallback path: tests simulate timeout and 503 end to end
  (check completes with method keyword_rules, severity capped at medium). Eval harness reports written to
  eval/private/reports/ (keyword, Haiku, Opus). docs/labeling-guide.md added; the harness validates the label file
  by line number.
- 2026-10-17, M5 (branch m5-photo-signals). Disk 37 GB free; measured before install: 69 MB wheels (~204 MB
  unpacked) + model files (int8 89 MB chosen; fp32 352 MB downloaded only to compare). CLIP ViT-B/32 (MIT) as int8
  ONNX on onnxruntime (no PyTorch); fp32 vs int8 same recall at precision 1.0. Fixtures: 24 CC0/public-domain
  Wikimedia Commons interiors (4.35 MB, manifest with source + licence per image; 3 candidates dropped after visual
  review), 12 edits each. eval/photo_dupes.py: 288 duplicate / 6,900 non-duplicate pairs (525 hard negatives);
  chosen rule single hash <= 10 OR both <= 18 OR cosine >= 0.95 -> precision 1.000, recall 0.774, 0 hard-negative
  hits (curve points in eval/reports/photo_dupes.md); crops removing >= 30% mostly missed. Latency per image on this
  machine (AMD, 12 logical CPUs): hashes 6.3 ms median, int8 embedding 100.5 ms median (p95 125 ms), model load
  0.6 s. Hash search: Postgres bit_count scan 7 ms @10k / 47 ms @100k vs BK-tree 24 / 257 ms -> SQL. Migration 0009
  round-trips; ruff, mypy strict (+ linux) clean; pytest 339/339 (116 integration). Live API + worker: listing A
  (3 photos, Oakville) -> photo_reuse_internal none; listing B (centre crop 80%, 50% resize, crop+resize; Mississauga)
  -> high: photo 2 pHash 0 / dHash 0 / cos 0.975, photo 3 pHash 10 / dHash 9 / cos 0.962 (photo 1, the 80% crop,
  not matched); photo_reuse_internal took 4.7 s cold (first check in the worker) and 0.6 s warm for 3 photos.
  photo_reuse_web: OFF, skipped; tested with fakes and a mock transport only; no TinEye account or call.

- 2026-10-17, M6 (branch m6-contact-graph). Backlog section added (crop-robust matching, TinEye
  verification); production model fetch + checksum noted in deployment.md and the M9 checklist; photo
  signals added to Humayun's .env. Migration 0010 (photo_links both ways, contact hash index) round-trips.
  photo_reuse_internal v2 stores each match (full M5 rule incl. embedding-only) in photo_links and shows
  other checks by public_ref (fixes P-010: M5 evidence exposed check ids). contact_reuse (deterministic)
  and graph.py (related checks, BFS cluster size with contacts as nodes, cap 500); GET /checks/{id} gains
  `related`, computed at read time. 355 tests pass (132 integration); ruff, mypy strict (+ linux),
  alembic check clean. EXPLAIN at 50,000 synthetic checks: graph queries 0.24-3.3 ms in Postgres with the
  indexes, 16-35 ms (seq scans) without; ~100 ms cluster search for a 300-check hotline is mostly a
  Docker Desktop transport stall on >8 kB parameters (P-009). Live API + worker: three checks sharing
  (905) 555-0142 and one photo list each other with linked_by [phone, photo], cluster 3, GET 38-39 ms;
  same-photo same-address pair is informational; no ids leaked.
