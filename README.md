# RentalScamChecker

A rental listing risk checker for students in Canada. Submit a listing; background jobs check the
price against public rent data, look for reused photos, extract red-flag language, and link reused
contacts across earlier submissions. The result is an explainable risk report, never a verdict.

Status: Milestone 0 (foundation). The API serves `GET /health`; the web home page shows it.

- `CLAUDE.md`: working rules for Claude Code (read first)
- `SPEC.md`: product and technical specification
- `TASKS.md`: milestones with "done when" criteria
- `docs/`: living documentation and the decision log

## Layout

```
apps/api/   FastAPI app + (later) worker, Python 3.12, SQLAlchemy 2, Alembic
apps/web/   Next.js (App Router, TypeScript strict, Tailwind)
docs/       architecture, decisions, testing, deployment, ...
docker-compose.yml   local Postgres 16 + pgvector
```

## Local setup

Prerequisites: Python 3.12, Node 24+, Git, Docker Desktop. Windows commands shown; on
macOS/Linux use `python3.12` and `.venv/bin/` instead of `py -3.12` and `.venv\Scripts\`.

Local ports: Postgres **5434**, API **8100**, web **3100**.

Windows: clone to a short path (e.g. `C:\Users\<you>\dev\`). If Windows long-path support is
off, `pip install` fails inside deeply nested folders (see docs/problems-and-solutions.md, P-004).

```powershell
git clone https://github.com/MhBaigtw/RentalScamChecker.git
cd RentalScamChecker
copy .env.example .env
# then set NOMINATIM_CONTACT_EMAIL (address_sanity) and ANTHROPIC_API_KEY (text_red_flags) in .env;
# the API won't start without them while those signals are enabled (ENABLED_SIGNALS)

# 1. Database
docker compose up -d --wait

# 2. API
cd apps\api
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m rentalscamchecker.photos.model_files   # CLIP image model, 89 MB, into var\models
# CMHC reference rents for price_vs_market (licence: data/reference/cmhc/README.md)
.venv\Scripts\python.exe -m rentalscamchecker.reference_rents ..\..\data\reference\cmhc\rms_average_rent_2025_10.csv
.venv\Scripts\python.exe -m pytest                 # creates/uses the separate *_test database
.venv\Scripts\python.exe -m rentalscamchecker      # serves http://127.0.0.1:8100
cd ..\..

# 3. Worker (second terminal): runs the background signal jobs
cd apps\api
.venv\Scripts\python.exe -m rentalscamchecker.worker   # Ctrl+C: finishes running jobs, then exits
cd ..\..

# 4. Web (third terminal)
cd apps\web
npm ci
npm run dev                                        # serves http://localhost:3100

# 5. Pre-commit hooks (once, from the repo root)
apps\api\.venv\Scripts\pre-commit.exe install
```

Check it works: `curl http://127.0.0.1:8100/health` returns `{"status":"ok","database":"ok"}`, and
http://localhost:3100 shows "All systems working". Stop Postgres (`docker compose stop`) and the
endpoint returns HTTP 503 with `{"error": {"code": "database_unavailable", ...}}`.

On Windows, run Python only through the venv; bare `python` may not be 3.12 (on the dev machine
it resolves to Cygwin). More detail, including the no-Docker fallback:
[docs/deployment.md](docs/deployment.md). How to run each check: [docs/testing.md](docs/testing.md).

## Limitations

Pre-alpha. Three risk signals run so far: price vs CMHC averages (Sheridan's area only: Toronto and
Hamilton CMAs), address checks via OpenStreetMap, and warning signs in the listing text (Anthropic's
Claude API, with a keyword fallback; no accuracy numbers yet, see docs/ai-ml-design.md). Scoring and the report UI come later; see
TASKS.md for the plan.
