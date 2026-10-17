// How each signal and its evidence are shown to people. Pure functions, unit tested.
// Wording rule (CLAUDE.md): risk signals with evidence, never a verdict about the listing.

import type { RiskLevel, Severity, SignalResult } from "./api";

export type SignalInfo = {
  title: string;
  // One line: what this check looks at.
  checks: string;
};

const SIGNALS: Record<string, SignalInfo> = {
  price_vs_market: {
    title: "Rent compared with the area",
    checks: "The monthly rent against CMHC average rents for the city and number of bedrooms.",
  },
  address_sanity: {
    title: "Address",
    checks: "Whether the address can be found on the map, in the city given, and is residential.",
  },
  text_red_flags: {
    title: "Listing text",
    checks: "The listing text for known warning signs, such as payment before a viewing.",
  },
  photo_reuse_internal: {
    title: "Photos seen before",
    checks: "The photos against every photo submitted to this service, including edited copies.",
  },
  photo_reuse_web: {
    title: "Photos elsewhere online",
    checks: "A reverse image search for the photos on the web.",
  },
  contact_reuse: {
    title: "Contact details",
    checks: "The phone number and email against other listings checked here.",
  },
};

export function signalInfo(name: string): SignalInfo {
  return SIGNALS[name] ?? { title: humanize(name), checks: "" };
}

export const LEVEL_LABELS: Record<RiskLevel, string> = {
  low: "Low risk",
  some: "Some risk signals",
  high: "High risk signals",
  incomplete: "Incomplete",
};

// A calm palette: no red alarms. Each pairs a tinted background with text that meets WCAG AA.
export const LEVEL_STYLES: Record<RiskLevel, { panel: string; dot: string }> = {
  low: { panel: "bg-emerald-50 border-emerald-200 text-emerald-950", dot: "bg-emerald-600" },
  some: { panel: "bg-amber-50 border-amber-200 text-amber-950", dot: "bg-amber-500" },
  high: { panel: "bg-orange-50 border-orange-200 text-orange-950", dot: "bg-orange-600" },
  incomplete: { panel: "bg-slate-50 border-slate-200 text-slate-900", dot: "bg-slate-500" },
};

export type Finding = { label: string; tone: "neutral" | "low" | "medium" | "high" | "muted" };

export function finding(result: SignalResult): Finding {
  if (result.status === "skipped") return { label: "Not checked", tone: "muted" };
  if (result.status === "error") return { label: "Couldn't complete", tone: "muted" };
  const bySeverity: Record<Severity, Finding> = {
    none: { label: "Nothing found", tone: "neutral" },
    low: { label: "Minor signal", tone: "low" },
    medium: { label: "Risk signal", tone: "medium" },
    high: { label: "Strong risk signal", tone: "high" },
  };
  return bySeverity[result.severity ?? "none"];
}

export const TONE_STYLES: Record<Finding["tone"], string> = {
  neutral: "bg-emerald-50 text-emerald-900 ring-emerald-200",
  low: "bg-sky-50 text-sky-900 ring-sky-200",
  medium: "bg-amber-50 text-amber-950 ring-amber-200",
  high: "bg-orange-50 text-orange-950 ring-orange-300",
  muted: "bg-slate-100 text-slate-700 ring-slate-200",
};

// --- evidence ---

export type EvidenceRow = { label: string; value: string | EvidenceRow[][] };

// Internal bookkeeping, not evidence a person can use.
const HIDDEN = new Set([
  "depends_on_other_checks",
  "start",
  "end",
  "prompt_version",
  "cached",
  "estimate_inputs",
]);

const LABELS: Record<string, string> = {
  method: "How we checked",
  listing_rent_cents: "Rent in the listing",
  reference_rent_cents: "Average rent used for comparison",
  percent_below_reference: "Below the average by",
  survey_year: "Survey year",
  cma: "Metropolitan area",
  rent_basis: "Average based on",
  baseline: "Comparison",
  photos_checked: "Photos checked",
  matches: "Matches",
  matched_check: "Earlier check",
  matched_city: "City of that check",
  match_type: "Kind of match",
  phash_distance: "Picture fingerprint difference (pHash)",
  dhash_distance: "Picture fingerprint difference (dHash)",
  cosine_similarity: "Image similarity (0 to 1)",
  same_address: "Same address",
  earlier_check_high_risk: "Earlier check had high risk signals",
  other_checks_at_different_address: "Other checks at a different address",
  other_cities: "Other cities",
  same_address_matches: "Matches at this same address",
  contacts_checked: "Contact details checked",
  other_checks: "Other checks with this contact",
  other_addresses: "Other addresses",
  linked_independent_levels: "Levels of those checks (from their own evidence)",
  linked_high_risk: "Linked checks with high risk signals",
  linked_checks: "Linked checks",
  own_signals: "Their own signals",
  linked_by: "Linked by",
  independent_risk_level: "Level from its own evidence",
  flags: "Warning signs found",
  label: "Sign",
  quote: "Quote from the listing",
  strength: "Strength",
  unverified_quotes_dropped: "Suggested quotes we couldn't find in the text (ignored)",
  fallback_reason: "Why the backup rules were used",
  found: "Found on the map",
  matched_address: "Map result",
  city_matches: "City matches",
  non_residential: "Looks non-residential",
  attribution: "Data source",
  source: "Source",
  source_url: "Source link",
  photo: "Photo",
  check: "Check",
  kind: "Kind",
};

// Where one key means different things in different signals.
const SIGNAL_LABELS: Record<string, Record<string, string>> = {
  address_sanity: { matched_city: "City on the map" },
};

const VALUES: Record<string, string> = {
  "perceptual_hash+clip_embedding": "Picture fingerprints and an image-similarity model",
  hmac_contact_lookup: "Encrypted fingerprints of the phone number and email",
  external_ai_api: "Claude (Anthropic's AI service) reading the text, with quotes checked",
  keyword_rules: "Keyword rules",
  "external_api:tineye": "TinEye reverse image search",
  hash: "Near-identical picture",
  embedding: "Very similar picture",
  field: "contact field",
  text: "listing text",
  vacant_units: "units for rent now",
  all_units: "all rented units",
  not_scored: "not scored yet",
  whole_unit: "whole unit",
  room: "room in a shared place",
  basement: "basement apartment",
  municipality: "the municipality",
  municipality_estimated_vacant: "the municipality (estimated from local averages)",
  no_reference_data: "No CMHC average for this city and size yet",
  room_not_comparable: "CMHC averages cover whole units, not rooms",
  no_contacts: "No phone number or email given",
  disabled: "Turned off",
};

export function humanize(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatValue(key: string, value: unknown, signal?: string): string | EvidenceRow[][] {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (key.endsWith("_cents")) return `$${(value / 100).toLocaleString("en-CA")}`;
    if (key.startsWith("percent")) return `${Math.round(value)}%`;
    return String(value);
  }
  if (typeof value === "string") return VALUES[value] ?? value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "None";
    if (value.every((v) => typeof v !== "object" || v === null)) {
      return value.map((v) => formatValue(key, v, signal)).join(", ");
    }
    return value
      .filter((v): v is Record<string, unknown> => typeof v === "object" && v !== null)
      .map((item) => evidenceRows(item, signal));
  }
  if (typeof value === "object") {
    const parts = Object.entries(value as Record<string, unknown>)
      .filter(([k]) => !HIDDEN.has(k))
      .map(([k, v]) => `${VALUES[k] ?? humanize(k)}: ${String(formatValue(k, v, signal))}`);
    return parts.length ? parts.join("; ") : "None";
  }
  return String(value);
}

export function evidenceRows(evidence: Record<string, unknown>, signal?: string): EvidenceRow[] {
  const overrides = (signal && SIGNAL_LABELS[signal]) || {};
  return Object.entries(evidence)
    .filter(([key]) => !HIDDEN.has(key))
    .map(([key, value]) => ({
      label: overrides[key] ?? LABELS[key] ?? humanize(key),
      value: formatValue(key, value, signal),
    }));
}

export function basedOnOtherListings(result: SignalResult): boolean {
  return result.evidence.depends_on_other_checks === true;
}
