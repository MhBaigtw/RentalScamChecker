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
