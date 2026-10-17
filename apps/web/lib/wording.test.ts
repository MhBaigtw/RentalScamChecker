// Wording rule (CLAUDE.md, M7): the web app never calls a listing a scam. Scans the source of
// every page, component and library file. The API's own strings are scanned by
// apps/api/tests/test_wording.py, and the e2e test scans a rendered report.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { VERDICT_PATTERNS } from "./wording";

const ROOT = join(__dirname, "..");
const DIRS = ["app", "components", "lib"];

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sourceFiles(path);
    // Tests, and the list of banned phrases itself, are not shown to users.
    const shown = /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name) && name !== "wording.ts";
    return shown ? [path] : [];
  });
}

describe("wording", () => {
  it("catches verdict phrasing and allows the brand name", () => {
    const hits = (text: string) => VERDICT_PATTERNS.some((p) => p.test(text));
    expect(hits("This listing is a scam")).toBe(true);
    expect(hits("Scam detected")).toBe(true);
    expect(hits("RentalScamChecker")).toBe(false);
    expect(hits("Some risk signals")).toBe(false);
  });

  it("no page, component or library file contains verdict wording", () => {
    const files = DIRS.flatMap((d) => sourceFiles(join(ROOT, d)));
    expect(files.length).toBeGreaterThan(5);
    const offending = files.flatMap((file) =>
      readFileSync(file, "utf8")
        .split("\n")
        .map((line, i) => ({ file, line: i + 1, text: line }))
        .filter(({ text }) => VERDICT_PATTERNS.some((p) => p.test(text))),
    );
    expect(offending).toEqual([]);
  });
});
