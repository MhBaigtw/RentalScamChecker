# Architecture

Target shape (SPEC.md section 7): a modular monolith. One Python codebase with two entrypoints
(HTTP API and, from M2, a worker), one Next.js front end, one Postgres database that also holds the
job queue.

```
browser ──HTTP──> apps/web (Next.js, :3100) ──server-side fetch──> apps/api (FastAPI, :8100) ──> Postgres 16 + pgvector (:5434)
                                                                     worker (M2) ───────────────┘
```

## What exists after M0

### API (`apps/api/rentalscamchecker/`)

| Module | Role |
|---|---|
| `config.py` | `Settings` (pydantic-settings). Reads env vars, then the repo-root `.env`. |
| `log_config.py` | structlog JSON logging for our logs and stdlib logs (uvicorn, SQLAlchemy); request-id middleware. |
| `db.py` | `make_engine()` (pre-ping, connect timeout) and `database_is_up()`. |
| `main.py` | `create_app(settings)` app factory; `GET /health`; `error_body()` (the one error shape). |
| `__main__.py` | `python -m rentalscamchecker` runs uvicorn on `API_PORT`. |
| `signals/` | Empty package; one module per signal from M2 on. |

The app factory takes a `Settings` object, so tests can build an app pointing at a deliberately
unreachable database without touching environment variables.

### Request flow: `GET /health`

1. `request_id_middleware` takes `X-Request-ID` from the request or generates a UUID, binds it to
   structlog's context vars (so every log line in that request carries it), and echoes it back.
2. The handler runs `SELECT 1` through the engine.
3. Success: `200 {"status": "ok", "database": "ok"}`.
   Any SQLAlchemy error: `503 {"error": {"code": "database_unavailable", ...}}`, logged as a warning.
4. The middleware logs one `request` line with method, path, status and duration.

The DB connect timeout (`DATABASE_CONNECT_TIMEOUT`, default 2 s) bounds how long a health check
can hang when the DB is down. On Docker Desktop the port proxy keeps accepting on 5434 even when
the container is stopped, so a DB-down check takes about the full timeout (measured 2.0 s).

### Web (`apps/web/`)

- `lib/health.ts`: `fetchHealth(apiUrl, fetchImpl)` maps every outcome to a typed status:
  `ok`, `degraded` (API answered with an error, e.g. 503) or `unreachable` (network error/timeout).
- `app/page.tsx`: the home page. Next.js 16 with Cache Components builds `/` as a partial
  prerender: the header is a static shell, and `<ApiHealth>` (inside `<Suspense>`) runs at request
  time after `await connection()`. The browser never calls the API directly in M0, so no CORS is
  needed yet (CORS lockdown is M8).

### Database

Alembic is set up with an empty baseline revision `0001_baseline`. Schema starts in M1.
