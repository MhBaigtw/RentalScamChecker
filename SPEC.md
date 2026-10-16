# SPEC.md — RentalScamChecker v1

## 1. Problem

Rental scams hit students and newcomers hardest: fake or stolen listings on Facebook Marketplace
and Kijiji, deposits demanded before any viewing, "landlord is out of the country" stories.
Rental sites verify their own listings, but the platforms where scams concentrate do not, and
the standard advice ("reverse image search the photos, check the address, compare the price")
is manual and inconsistently followed. RentalScamChecker automates that advice and adds memory across
submissions: a photo or phone number seen on an earlier risky listing is linked automatically.

## 2. Users

- Primary: students (especially international students) searching for housing in Ontario,
  starting with Sheridan College (Oakville, Brampton, Mississauga).
- Secondary: campus housing offices and student unions who point students to the tool.

## 3. Core user journey (v1)

1. User opens the site (no account needed) and fills a form: listing URL (optional, stored as
   text only, never fetched), listing text, monthly rent, bedrooms, address or intersection,
   city, contact phone/email (optional), 1 to 10 photos.
2. API validates, stores the submission, enqueues a check, returns a check id.
3. The page polls `GET /checks/{id}` (or SSE later) and shows progress per signal.
4. Workers run each signal independently; when all finish (or time out), the aggregator computes
   the risk level and the report.
5. User sees the report: overall level, each signal with evidence and next steps.
6. Optional: signed-in users see their history and are asked later "How did this turn out?"
   (rented safely / it was a scam / didn't proceed / unsure). These outcomes become labels.
7. A listed landlord can dispute a flag through a contact form; disputes are logged and reviewed.

## 4. Signals (v1)

Each signal module implements the same interface:
`run(check: CheckInput, ctx: SignalContext) -> SignalResult` where SignalResult has
`name, status (ok | skipped | error), severity (none | low | medium | high), evidence (JSON),
explanation (user text), next_step (user text), version`.

| Signal | Logic | Type |
|---|---|---|
| price_vs_market | Compare rent to CMHC average rent for the CMA (city) and bedroom count; zone level later if zone boundaries are obtainable. Flag when rent is far below the reference (threshold configurable, start at 30% below). | Deterministic rule on public data |
| text_red_flags | LLM extracts a fixed schema of flags from listing text and any pasted messages: deposit_before_viewing, owner_unavailable_abroad, keys_by_mail, urgency_pressure, off_platform_payment (gift cards, crypto, wire), refuses_viewing, too_good_to_be_true_language. Output is JSON validated against a schema; each flag includes the quoted span that triggered it. Cached by content hash. Deterministic keyword fallback if the API fails. | External LLM API (Anthropic) + rules |
| photo_reuse_internal | Perceptual hashes (pHash, dHash) of each photo; Hamming-distance search against all previously seen photos; image embeddings (pretrained CLIP-style model, run locally on CPU) in pgvector to catch crops. Flag when a photo matches a photo from a listing with a different address/city or from a previously high-risk check. | Pretrained model + deterministic hashing |
| photo_reuse_web | Reverse image search through TinEye API. Feature-flagged OFF by default; daily spend cap. | Paid external API |
| contact_reuse | Normalize phone (E.164) and email; HMAC hash; look up prior checks with the same hash; flag when linked to other addresses or prior high-risk checks. | Deterministic + graph lookup |
| address_sanity | Geocode the address (Nominatim with caching and a proper User-Agent, respecting its usage policy; swap for a paid geocoder later if needed). Flag when it doesn't resolve, resolves to a different city than claimed, or resolves to a non-residential place. | Deterministic + external API |

## 5. Scoring (v1)

- Explainable, rule-based aggregation: each signal contributes a weight by severity; a small
  set of "critical" combinations escalates directly (e.g. photo reused at a different address
  AND deposit before viewing).
- Output levels: Low risk / Some risk signals / High risk signals, plus "Incomplete" when key
  signals errored.
- Weights and thresholds live in a versioned config file; every report stores the scoring
  version used.
- v2 (only if enough labeled outcomes exist): train a classifier (logistic regression or
  gradient boosting) on signal features; compare against the rule baseline on a held-out set;
  ship only if it beats the baseline, and report precision/recall honestly.

## 6. Scam graph

Nodes: check, photo (by perceptual hash cluster), contact (by HMAC hash), address (geocoded).
Edges: check-uses-photo, check-uses-contact, check-at-address. Stored relationally in Postgres
(no graph database). Queries: "other checks sharing a photo or contact with this one" and
"cluster size". Entity resolution rules (when two photos are "the same") are documented and
tested with fixtures.

## 7. Architecture

- Modular monolith.
- `apps/web`: Next.js App Router, TypeScript, Tailwind. Calls the API over HTTPS.
- `apps/api`: FastAPI app (HTTP) and a worker process (same codebase, different entrypoint).
- Postgres 16 with pgvector: all data, plus the job queue.
- Job queue: a `jobs` table consumed with `SELECT ... FOR UPDATE SKIP LOCKED`; retries with
  exponential backoff, max attempts, visibility timeout (lease), idempotency key per
  (check_id, signal_name, signal_version), dead-letter status. Chosen over Redis/Celery to keep
  infra minimal and to make concurrency explicit and explainable; revisit if throughput demands.
- File storage: an interface with a local-disk implementation for dev; S3-compatible storage
  for production (decide at deploy time). Images are re-encoded and EXIF-stripped on upload.
- Observability: structured JSON logs (structlog), request/job ids, basic metrics (job latency,
  failures per signal, queue depth) exposed at an internal endpoint.

## 8. Data model (initial; Alembic migrations)

- `checks` (id uuid, created_at, status, risk_level, scoring_version, user_id nullable,
  submitted_url text, listing_text, rent_cents, bedrooms, address_text, city, geocode fields)
- `check_photos` (id, check_id, storage_key, phash bigint, dhash bigint, embedding vector,
  width, height)
- `check_contacts` (id, check_id, kind phone|email, hmac_hash bytea)
- `signal_results` (id, check_id, name, version, status, severity, evidence jsonb,
  explanation, next_step, started_at, finished_at, error)
- `jobs` (id, kind, payload jsonb, idempotency_key unique, status queued|running|done|failed|dead,
  attempts, max_attempts, run_after, locked_until, last_error, created_at, updated_at)
- `outcomes` (id, check_id, user_id, outcome, note, created_at)
- `disputes` (id, check_id, contact_email, message, status, created_at)
- `users` (id, email, created_at) — auth provider decided in Milestone 8
- `reference_rents` (cma, bedrooms, avg_rent_cents, survey_year, source)
- `llm_cache` (content_hash, prompt_version, model, response jsonb, created_at)

## 9. API (v1)

- `POST /checks` multipart (fields + photos) -> 202 {check_id}
- `GET /checks/{id}` -> status, per-signal progress, report when complete
- `POST /checks/{id}/outcome` (auth) -> 201
- `POST /checks/{id}/dispute` -> 201
- `GET /health` and `GET /internal/metrics` (internal)
- Rate limits: per IP on POST /checks; stricter for anonymous users.
- Errors use one JSON shape: {error: {code, message, details}}.

## 10. Security and abuse

- Input validation and size limits (photos: count, bytes, dimensions, allowed types).
- Image decoding hardened (Pillow limits; reject decompression bombs).
- Rate limiting and basic bot protection on submission.
- Adversarial use: scammers may test listings against the checker. Do not expose raw rule
  thresholds or weights in the UI; log submission patterns; per-IP and per-contact-hash limits.
- Secrets only via env; HMAC pepper rotation documented.
- Retention: raw listing text and photos deleted after a configurable period (default 180 days);
  hashes and signal results kept for the graph.
- CORS restricted to the web origin.

## 11. Evaluation

- `eval/` contains harnesses, not data. Labeled data lives in `eval/private/` (gitignored)
  because it contains third-party listing text.
- text_red_flags: per-flag precision/recall on a hand-labeled set (Humayun labels; target
  150 to 300 examples). Report the set size alongside every number.
- photo_reuse_internal: precision/recall of near-duplicate matching on a fixture set built by
  applying crops/resizes/recolors to sample images, plus true non-duplicates.
- Overall scoring: confusion matrix on whatever real outcomes exist; if fewer than ~50 labeled
  outcomes, say the number is too small to conclude anything.

## 12. Out of scope for v1

Native mobile apps, browser extension (v1.1), automated scraping of any platform, payments,
landlord accounts, multi-language UI, zone-level CMHC matching unless boundaries are available.

## 13. Open questions (resolve in the noted milestone)

- Can CMHC zone boundaries be obtained for zone-level price comparison? (M3)
- Which embedding model gives acceptable crop detection on CPU? (M5)
- Auth provider: Auth.js with Google OAuth vs magic link vs Supabase Auth (M8)
- Hosting for API + worker + Postgres with pgvector, and its monthly cost (M9; ask before spending)
