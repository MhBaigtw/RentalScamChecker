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
