import { defineConfig } from "vitest/config";

// Unit tests only; the browser tests in e2e/ run with Playwright (npm run e2e).
export default defineConfig({
  test: {
    include: ["lib/**/*.test.ts"],
  },
});
