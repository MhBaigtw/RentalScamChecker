// Browser end-to-end test (M7): the real web app, API, worker and Postgres. See docs/testing.md.
//
// Ports 3110 / 8110 (not the dev 3100 / 8100), its own database (<dev db>_e2e_test) and photo
// folder, and only signals that make no network calls. The backend process is
// apps/api/tests/e2e_server.py.

import { defineConfig, devices } from "@playwright/test";

const WEB_PORT = 3110;
const API_PORT = 8110;
const python =
  process.env.E2E_PYTHON ??
  // cmd.exe runs the command on Windows, so the path needs backslashes there.
  (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // npm run e2e
    { name: "journey", grepInvert: /@screenshots/, use: { ...devices["Desktop Chrome"] } },
    // npm run e2e:screenshots: images for docs/screenshots, on request only.
    { name: "screenshots", grep: /@screenshots/, use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: [
    {
      command: `${python} -m tests.e2e_server`,
      cwd: "../api",
      url: `http://127.0.0.1:${API_PORT}/health`,
      env: {
        E2E_API_PORT: String(API_PORT),
        E2E_WEB_ORIGIN: `http://127.0.0.1:${WEB_PORT}`,
      },
      timeout: 120_000,
      reuseExistingServer: false,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: `npx next build && npx next start -p ${WEB_PORT} -H 127.0.0.1`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: {
        NEXT_PUBLIC_API_URL: `http://127.0.0.1:${API_PORT}`,
        API_URL: `http://127.0.0.1:${API_PORT}`,
      },
      timeout: 240_000,
      reuseExistingServer: false,
    },
  ],
});
