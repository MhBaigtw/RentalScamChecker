# Problems and solutions

Format: symptom, root cause, fix, lesson.

## P-001: Postgres container "healthy" but refusing connections  (2026-10-16, M0)
- Symptom: `docker compose up --wait` returned with the container healthy, and the next `psql`
  failed with `FATAL: the database system is starting up`.
- Root cause: on first boot the image runs init with a temporary server that listens only on the
  Unix socket, then restarts. `pg_isready` without `-h` checks the socket, so it reported ready
  during the temporary phase.
- Fix: healthcheck uses TCP, `pg_isready -h 127.0.0.1 ...`. The temporary server doesn't listen
  on TCP. Verified by recreating the volume from scratch and querying immediately after `--wait`.
- Lesson: a health check must test the same path the clients use.

## P-002: `tsc` fails on a fresh clone with `Cannot find name 'LayoutProps'`  (2026-10-16, M0)
- Symptom: typecheck passed locally, failed after deleting `next-env.d.ts` and `.next/`.
- Root cause: Next 16's global route types (`LayoutProps`, `PageProps`) are generated files,
  gitignored, created by `next dev`/`next build`.
- Fix: `npm run typecheck` runs `next typegen && tsc --noEmit`.
- Lesson: test the fresh-clone state, not just the working copy.

## P-003: vitest install failed with ERESOLVE  (2026-10-16, M0)
- Symptom: `npm install -D vitest` refused: vitest 5 wants `@types/node` ≥22, scaffold pinned `^20`.
- Fix: `@types/node@^24` to match the actual Node 24 runtime; `engines.node` set to `>=24`.
  Not `--legacy-peer-deps`, which would hide a real mismatch.

## P-004: `pip install` fails in a deeply nested clone on Windows  (2026-10-16, M0)
- Symptom: during the fresh-clone check, `pip install -e ".[dev]"` failed with
  `Could not install packages ... .dll` and pip's hint about Windows Long Path support.
- Root cause: the clone was under a very long temp path; paths inside `.venv\Lib\site-packages`
  passed Windows' 260-character limit. `LongPathsEnabled` is 0 on the dev machine.
- Fix: clone to a short path (`C:\Users\MhBai\dev\...`); the same steps then passed. Alternative
  (needs admin, not done): set `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem\LongPathsEnabled`
  to 1.
- Lesson: setup docs must state path assumptions that aren't obvious.

## P-005: Manual curl test sent 3 photos but the API saw none  (2026-10-16, M1)
- Symptom: during the M1 end-to-end check, `curl -F 'contact_phone=(905) 555-0142' -F photos=@p1.jpg ...`
  returned `contact_phone: Not a valid phone number` and `photos: Upload between 1 and 10 photos`.
  The automated tests (TestClient) passed, and the same number was valid when checked in Python.
- Root cause: in curl's `-F` syntax, a value that starts with `(` opens a nested
  `multipart/mixed` part. curl folded the photos into the phone field. Seen with `--trace-ascii`.
  The API was correct.
- Fix: use `--form-string` for text fields (sent literally), `-F name=@file` only for files.
- Lesson: when an end-to-end check disagrees with the tests, inspect the raw request before
  changing the code.

## P-006: Status polling looked ~2 s per request slower than the API logs said  (2026-10-16, M2)
- Symptom: a Python script polling `http://localhost:8100` saw its first response after 4.4 s,
  while the API's own logs showed requests taking milliseconds.
- Root cause (verified pre-M3): on Windows, `localhost` resolves to `['::1', '127.0.0.1']`;
  uvicorn listens on `127.0.0.1` only, so the client first tries `::1`, and Windows reports the
  refused connection only after about 2 s (it retries refused connects), then falls back to IPv4.
  Measured with urllib against `/health`: `localhost` 2,075 ms and 2,061 ms, `127.0.0.1` 21 ms
  and 12 ms; a raw `::1` connect was refused after 2,006 ms.
- Fix: use `http://127.0.0.1:8100` in scripts. Then: POST 0.12 s, pending -> running -> complete
  visible at 0.16 s / 0.66 s / 1.72 s.
- Lesson: when a client-side timing disagrees with server logs, suspect the connection
  (DNS/IPv6) before the server.
- Follow-up (pre-M3): `API_URL` in `.env.example` and the web app's `DEFAULT_API_URL` now use
  `http://127.0.0.1:8100` for the same reason. Postgres URLs keep `localhost`: Docker publishes
  port 5434 on IPv6 too (`[::]:5434`), so there is no fallback delay there.

## P-007: mypy passed on Windows, failed in CI on Linux  (2026-10-16, M2)
- Symptom: CI's `mypy tests alembic` reported `Module has no attribute "CREATE_NEW_PROCESS_GROUP"`
  and `"CTRL_BREAK_EVENT"` in `tests/test_worker_process.py`; locally mypy was clean.
- Root cause: typeshed declares those constants only for Windows. mypy checks for the platform
  it runs on, and a runtime `IS_WINDOWS` variable doesn't narrow anything for it.
- Fix: select them under `if sys.platform == "win32": ... else: ...`, a check mypy evaluates
  statically. Verified locally with `mypy --platform linux` as well as the default.
- Lesson: platform-specific code needs a type check per platform; `mypy --platform linux` catches
  this before CI does.

## P-008: "Caledon" and "Milton" had no reference rent: the province-suffix regex ate the name  (2026-10-16, M3)
- Symptom: the CMA-fallback tests for Caledon and Milton failed with `None`, although the
  municipal and CMA rows were in the database, and each half of the lookup worked when called
  separately.
- Root cause: `normalize_city` stripped a trailing province with `r",?\s*(on|ont|ontario)\.?$"`.
  The comma and the space were both optional, so it also removed the final "on" of Caledon,
  Milton, Burlington and Kingston ("caled", "milt"...). Two tests (Burlington, Kingston expected
  `None`) passed by accident.
- Fix: the suffix must follow a comma or whitespace: `r"(,\s*|\s+)(on|ont|ontario)\.?$"`.
  Regression cases for those four names added. The same function is used by address_sanity's
  city comparison, which had the same bug.
- Lesson: a test whose expected value is "nothing found" can pass for the wrong reason; check
  that a "None" result is None for the reason you think.
