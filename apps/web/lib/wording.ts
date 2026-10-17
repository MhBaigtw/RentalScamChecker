// Verdict phrasing the product must never use (CLAUDE.md product rules). Shared by the unit
// test (lib/wording.test.ts) and the browser test (e2e/), and kept in step with
// apps/api/tests/test_wording.py.

export const VERDICT_PATTERNS = [
  /\b(is|looks like|appears to be|seems to be|must be|probably|likely|definitely|certainly)\s+(an?\s+)?(scam|fraud|fake)\b/i,
  /\bscam\s+(detected|confirmed|alert|listing|warning|likely)\b/i,
  /\b(likely|probable|possible|confirmed|known|obvious)\s+scam\b/i,
  /\b(fraudulent|fake|bogus)\s+(listing|ad|post|landlord|rental)\b/i,
  /\bthis\s+(listing|ad|post|rental)\s+is\s+(fraud|fake|fraudulent|a\s+scam)\b/i,
  // "scam" as a word of its own (the brand name RentalScamChecker is not one).
  /\bscam(s|mer|mers|ming|my)?\b/i,
];
