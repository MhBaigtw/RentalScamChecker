import { describe, expect, it } from "vitest";

import type { SignalResult } from "./api";
import { LEVEL_LABELS, evidenceRows, finding, signalInfo } from "./signals";

function result(overrides: Partial<SignalResult>): SignalResult {
  return {
    name: "price_vs_market",
    version: "1",
    status: "ok",
    severity: "none",
    evidence: {},
    explanation: null,
    next_step: null,
    finished_at: null,
    ...overrides,
  };
}

describe("finding", () => {
  it("describes severities without verdicts", () => {
    expect(finding(result({ severity: "high" })).label).toBe("Strong risk signal");
    expect(finding(result({ severity: "none" })).label).toBe("Nothing found");
    expect(finding(result({ status: "skipped", severity: null })).label).toBe("Not checked");
    expect(finding(result({ status: "error", severity: null })).label).toBe("Couldn't complete");
  });
});

describe("evidenceRows", () => {
  it("formats money, percentages, booleans and lists, and hides bookkeeping keys", () => {
    const rows = evidenceRows({
      listing_rent_cents: 120000,
      percent_below_reference: 41.6,
      same_address: false,
      other_cities: ["Oakville", "Milton"],
      depends_on_other_checks: true,
      start: 3,
    });
    expect(rows).toEqual([
      { label: "Rent in the listing", value: "$1,200" },
      { label: "Below the average by", value: "42%" },
      { label: "Same address", value: "No" },
      { label: "Other cities", value: "Oakville, Milton" },
    ]);
  });

  it("renders lists of objects as nested rows", () => {
    const rows = evidenceRows({ flags: [{ label: "Asks for a deposit", quote: "send $500 first" }] });
    expect(rows[0].label).toBe("Warning signs found");
    expect(rows[0].value).toEqual([
      [
        { label: "Sign", value: "Asks for a deposit" },
        { label: "Quote from the listing", value: "send $500 first" },
      ],
    ]);
  });

  it("uses a signal's own label where a key means something different there", () => {
    expect(evidenceRows({ matched_city: "Milton" }, "address_sanity")[0].label).toBe(
      "City on the map",
    );
    expect(evidenceRows({ matched_city: "Milton" }, "photo_reuse_internal")[0].label).toBe(
      "City of that check",
    );
  });
});

describe("labels", () => {
  it("uses exactly the allowed level names", () => {
    expect(Object.values(LEVEL_LABELS).sort()).toEqual(
      ["High risk signals", "Incomplete", "Low risk", "Some risk signals"].sort(),
    );
  });

  it("has a title for every signal the API runs", () => {
    for (const name of [
      "price_vs_market",
      "address_sanity",
      "text_red_flags",
      "photo_reuse_internal",
      "photo_reuse_web",
      "contact_reuse",
    ]) {
      expect(signalInfo(name).checks).not.toBe("");
    }
  });
});
