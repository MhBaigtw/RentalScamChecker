# Deployment and local setup

Production deployment is decided in M9. This file currently covers local development.

## Ports

| Service | Host port | Why not the default |
|---|---|---|
| Postgres (Docker) | 5434 | 5432: a native Postgres 16 service on the dev machine; 5433: another project's container |
| API (uvicorn) | 8100 | 8000 was used by another project |
| Web (Next.js) | 3100 | 3000 was used by another project |

All are configurable: `POSTGRES_PORT`, `DATABASE_URL`, `API_PORT`, `API_URL` in `.env`
(see `.env.example`); the web port is set in `apps/web/package.json` scripts.

## Primary path: Docker Compose (verified working 2026-10-16)

`docker compose up -d --wait` starts `pgvector/pgvector:pg16` (Postgres 16.15, pgvector 0.8.7 at
time of writing) with a named volume `rentalscamchecker_pgdata`. The healthcheck uses TCP
(`pg_isready -h 127.0.0.1`) so `--wait` only returns once the real server accepts connections
(see docs/problems-and-solutions.md, P-001).

Environment checked in M0 on Humayun's machine: Windows 11, Docker Desktop 29.2.1 with Compose
v5.1.0 (WSL 2 backend, ~3.5 GiB VM memory), Python 3.12.6 via `py -3.12`, Node 24.14.1,
Git 2.55.0, about 26 GB free on C:.

Docker Desktop doesn't necessarily start with Windows. If `docker version` reports it can't
connect to `dockerDesktopLinuxEngine`, start Docker Desktop and wait for the daemon (seen at the
start of M1; the container and its data were intact afterwards).

## Secrets and local files (M1)

- `CONTACT_HASH_PEPPER` (required, ≥ 32 chars): key for contact HMACs (ED-004). `.env.example`
  has a dev-only value. Every real environment needs its own random value,
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`, kept in the host's secret store.
  It must not change once real data exists (all stored hashes would stop matching).
- `PHOTO_STORAGE_DIR` (default `var/photos`, relative to the repo root, gitignored): local-disk
  photo storage for development.

## Reference data and external services (M3)

- Load CMHC reference rents after migrating:
  `python -m rentalscamchecker.reference_rents ../../data/reference/cmhc/rms_average_rent_2025_10.csv`
  (an upsert; safe to re-run). Without it, price_vs_market returns `skipped` for every check.
- `NOMINATIM_CONTACT_EMAIL` is required while `address_sanity` is enabled; the API and the worker
  refuse to start without it. The public Nominatim service allows at most 1 request/s for our
  whole deployment, which the `rate_limits` row enforces across worker processes (ED-022).
- `/internal/metrics`: set `INTERNAL_METRICS_TOKEN` in any shared environment (ED-023).
- `ANTHROPIC_API_KEY` is required while `text_red_flags` is enabled (M4). Calls are paid: keep
  `LLM_DAILY_CALL_CAP` low (default 200) until spend is approved, and watch `llm_calls` for token use.

## Running the worker (M2)

The worker is a second process from the same codebase: `python -m rentalscamchecker.worker`
(from `apps/api`, through the venv). Without it, checks stay `pending`. Settings
(`WORKER_CONCURRENCY`, `JOB_LEASE_SECONDS`, `CHECK_TIMEOUT_SECONDS`, ...) are listed in
`.env.example`. Stopping: Ctrl+C (or SIGTERM in production) lets running jobs finish, then
exits 0. A hard kill is also safe: the interrupted job is handed out again when its lease
expires (default 60 s). Run more worker processes to scale out.

## Fallback: no Docker

Use this only if Docker Desktop stops working. Not exercised in M0 because Docker worked.

1. Native PostgreSQL 16. The dev machine already has the EDB installer build
   (`postgresql-x64-16` service, port 5432). Create a role and database:
   `psql -U postgres -c "CREATE ROLE rsc LOGIN PASSWORD '...'; CREATE DATABASE rentalscamchecker OWNER rsc;"`
   and point `DATABASE_URL` at port 5432.
2. pgvector is **not** installed in that native build (checked: no `vector.control` under
   `C:\Program Files\PostgreSQL\16\share\extension`). Installing it on Windows means building
   from source with the Visual Studio C++ build tools (`nmake /F Makefile.win`, per the pgvector
   README), which is a large install. pgvector is first needed in M5.
3. If building pgvector natively is not practical, a hosted dev database with pgvector (for
   example a free Neon project) is acceptable, **but ask Humayun first** (new external service).

## Production

TBD in M9 (options and monthly cost presented before any spend).
