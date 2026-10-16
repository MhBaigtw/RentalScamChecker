# Tech stack

One line of "why" per dependency (CLAUDE.md rule). Versions are what M0 installed.

## Infrastructure

| What | Version | Why | Alternatives |
|---|---|---|---|
| PostgreSQL + pgvector (`pgvector/pgvector:pg16`) | 16.15 / 0.8.7 | All data, the job queue (ED-002) and image embeddings (M5) in one database | Separate vector DB (another service to run) |
| Docker Compose | v5.1.0 | One command for the local DB; same image as CI | Native install (fallback, docs/deployment.md) |
| GitHub Actions | n/a | CI next to the repo, Postgres as a service container | Other CI services |

## API (`apps/api`, Python 3.12)

Standard library also does real work: `hmac` + `hashlib` for contact hashes (ED-004).

| Package | Pin | Why |
|---|---|---|
| fastapi | ~=0.143.2 | Typed HTTP framework with validation and OpenAPI out of the box |
| uvicorn[standard] | ~=0.54.0 | ASGI server that runs FastAPI |
| sqlalchemy | ~=2.1.4 | SQL toolkit/ORM; 2.x typed API; used by Alembic |
| psycopg[binary] | ~=3.3.6 | Postgres driver (v3); binary wheel avoids a C build on Windows |
| alembic | ~=1.20.0 | Schema migrations for SQLAlchemy |
| pydantic-settings | ~=2.16.0 | Typed settings from env vars / `.env` |
| structlog | ~=26.1.0 | Structured JSON logs with context vars (request id / job id) |
| python-multipart | ~=0.0.32 | Required by FastAPI to parse `multipart/form-data` (`POST /checks`) (M1) |
| pillow | ~=12.3.0 | Decode, validate and re-encode uploaded photos; bomb protection; also needed by imagehash in M5 (M1) |
| phonenumbers | ~=9.0.41 | Port of Google's libphonenumber: parse and validate phone numbers, normalize to E.164 (M1) |
| openpyxl | (script only, not an app dependency) | Reads CMHC's Excel data tables in `data/reference/cmhc/fetch_rms.py`; installed by hand when refreshing the dataset |
| httpx | ~=0.28.1 | HTTP client for the Nominatim geocoder (timeouts, injectable transport for tests) and the CMHC fetch script. Was already a dev dependency (TestClient); runtime since M3 |

Dev: pytest (tests), ruff (lint + format), mypy (strict type checks), pre-commit (git hooks).

External services (no library): OpenStreetMap Nominatim (geocoding, free, usage policy in ED-022;
data under ODbL, attribution required). Reference data: CMHC Rental Market Survey via CMHC's
Housing Market Information Portal (ED-021; licence in `data/reference/cmhc/README.md`).

Known: Starlette warns that using `httpx` with its TestClient is deprecated in favour of
`httpx2`. Tests still pass; switch when FastAPI's docs recommend it.

## Web (`apps/web`, Node 24)

| Package | Version | Why |
|---|---|---|
| next | 16.4.2 | React framework, App Router, server components; matches existing experience (ED-003) |
| react / react-dom | 19.3.0 | Required by Next |
| tailwindcss (+ @tailwindcss/turbopack) | ^4 | Utility CSS; scaffolded by create-next-app |
| typescript | ^5 | `strict: true` |
| eslint + eslint-config-next | ^9 / 16.4.2 | Lint rules from Next.js |
| vitest | ^5.0.5 | Fast unit tests for TS without a browser |
| @types/node | ^24 | Matches the Node 24 runtime; vitest 5 requires ≥22 |

Known: `npm audit` reports 5 high-severity findings, all one chain inside
`eslint-config-next` → `fast-glob` → `micromatch` → `braces` (ReDoS). Dev-only lint tooling,
not shipped to users. npm's suggested fix downgrades to `eslint-config-next@14`, which would be
worse. Revisit in the M9 security pass. ESLint 9 is also flagged as deprecated upstream; same.
