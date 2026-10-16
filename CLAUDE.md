# CLAUDE.md — RentalScamChecker

Read this file, then SPEC.md, then TASKS.md, at the start of every session.

## What this project is

RentalScamChecker is a rental listing risk checker for students in Canada.
A user submits a listing (text, photos, price, address, contact details). Background jobs run
independent checks and produce an explainable risk report. Every check also feeds a shared
"scam graph" that links reused photos, phone numbers and emails across listings.

The owner is Humayun, a CS student building this as a portfolio project for Summer 2027
SWE / backend / AI / data engineering internships. After the build, he will study the codebase
in depth and must be able to explain every decision in interviews. Write code he can explain.

## Working mode

- Build first, teach later. During implementation keep explanations short; record the reasoning
  in docs/ instead of lecturing in chat.
- Work one milestone at a time from TASKS.md. Do not start the next milestone until the
  current one's "Done when" checks pass.
- Git: one branch and one PR per milestone (branch `mN-<short-name>`, PR titled
  "MN: <name>"). Wait for CI to pass on the PR, then merge with a merge commit (no squash,
  no rebase) to keep the history. Never force-push to `main`.
- For every milestone:
  1. State what you are building (2 to 4 lines).
  2. Note any significant decision briefly and add it to docs/engineering-decisions.md.
  3. Implement.
  4. Run the relevant tests, linters and type checks.
  5. Fix failures.
  6. Verify the feature works end to end (run it, hit the endpoint, show real output).
  7. Summarize what changed in a few lines.
  8. Update docs/ (see docs/README.md for which files) and tick the milestone in TASKS.md.
- Do not ask for approval on routine implementation details. DO ask before:
  - destructive actions (dropping data, deleting files outside the repo, force pushes, `rm -rf`)
  - anything that costs money (paid APIs, cloud resources, domain purchases)
  - major architecture changes that contradict SPEC.md or a logged decision
  - adding a new external service or a heavy dependency

## Honesty rules (non-negotiable)

- Never claim something works unless you ran it and saw it work. If you only compiled it, say so.
- Never invent test results, benchmarks, accuracy numbers, user counts or performance figures.
- If a test is skipped, flaky or disabled, say so explicitly.
- Distinguish clearly, in code comments and docs: pretrained models we integrate, external AI
  APIs we call, models we train ourselves, and plain deterministic rules. Never describe an API
  call as "our model".
- If you find a bug or a wrong assumption in earlier work (yours or Humayun's), raise it
  immediately; don't bury it.

## Product rules

- Never label a listing "a scam" in user-facing text. The product reports "risk signals" with
  evidence. Allowed levels: Low risk / Some risk signals / High risk signals.
  Note: the product name contains "Scam". The wording test (M7) must target verdict phrasing
  about a listing ("is a scam", "scam detected", etc.), not the brand name.
- Every signal shown to a user must carry: what was checked, what was found, the evidence,
  and a plain-language next step.
- No scraping of Facebook Marketplace, Kijiji, Craigslist or any site whose terms forbid it.
  Listings enter the system only by user submission (and later, a browser extension reading the
  page the user already has open).
- Privacy: phone numbers and emails are normalized, then stored as HMAC-SHA256 hashes keyed with
  a secret pepper (env var). Raw contact values are not persisted beyond the processing job.
- Paid services (e.g. TinEye) stay behind a feature flag that defaults to OFF, with a daily
  spend cap, until Humayun approves the spend.

## Engineering style

- Simple first. Modular monolith. No microservices, no Kubernetes, no premature abstraction.
- Prefer boring, well-known libraries. Each new dependency needs a one-line reason in
  docs/tech-stack.md.
- Small functions, clear names, type hints everywhere (Python: mypy strict on app code;
  TypeScript: strict).
- Make the minimal change that solves the task. Don't refactor unrelated code.
- Tests live next to the code they test in spirit: unit tests for pure logic, integration
  tests against a real Postgres, a few end-to-end tests for the main user journey.
- Structured JSON logs with a request id / job id on every line.
- Configuration via environment variables only; `.env.example` lists every variable.
  Never commit secrets.

## Environment notes (Humayun's machine)

- Windows. `py` defaults to Python 3.13, so the API uses a venv created with
  `py -3.12 -m venv .venv` inside `apps/api/`. Run everything through it
  (`apps/api/.venv/Scripts/python.exe -m pip ...`, `... -m pytest`). Never use bare `python`:
  on this machine it resolves to Cygwin's Python (`C:\Tools\cygwin64\bin\python`).
- Local ports (5432, 5433, 3000, 8000 are taken by other projects): Postgres 5434, API 8100,
  web 3100. Recorded in `.env.example` and docs/deployment.md.
- Docker Desktop has crashed on this machine before and the C: drive has been low on space.
  In Milestone 0, verify Docker works before relying on it. If it doesn't, fall back to a
  native PostgreSQL 16 install and document the steps; check that pgvector is available
  (it can be harder to install natively on Windows; a hosted dev database such as a free
  Neon project is an acceptable fallback, but ask first).
- Before running anything long or disk-heavy, check free disk space.

## Repository map

- `apps/web/` Next.js (App Router, TypeScript, Tailwind) front end
- `apps/api/` FastAPI app + worker (Python 3.12), SQLAlchemy 2, Alembic
- `apps/api/rentalscamchecker/signals/` one module per signal
- `data/reference/` small reference datasets (e.g. CMHC rents) and their loaders
- `eval/` evaluation harness; labeled data lives in `eval/private/` (gitignored)
- `docs/` living documentation (see docs/README.md)
