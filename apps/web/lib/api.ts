// Typed client for the RentalScamChecker API (docs/api-design.md). The browser calls the API
// directly; the API allows the web origin through CORS (ED-028).

// 127.0.0.1 rather than localhost: see docs/problems-and-solutions.md P-006.
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8100";

export type Severity = "none" | "low" | "medium" | "high";
export type RiskLevel = "low" | "some" | "high" | "incomplete";

export type SignalResult = {
  name: string;
  version: string;
  status: "ok" | "skipped" | "error";
  severity: Severity | null;
  evidence: Record<string, unknown>;
  explanation: string | null;
  next_step: string | null;
  finished_at: string | null;
};

export type Risk = {
  level: RiskLevel;
  label: string;
  summary: string;
  contributing: string[];
  missing: string[];
};

export type RelatedCheck = {
  check: string;
  created_on: string;
  city: string;
  risk_level: RiskLevel | null;
  linked_by: string[];
  same_address: boolean;
};

export type Related = {
  explanation: string;
  next_step: string;
  total: number;
  at_different_address: number;
  checks: RelatedCheck[];
  cluster_size: number;
  cluster_size_capped: boolean;
};

export type Check = {
  id: string;
  status: "pending" | "running" | "complete";
  created_at: string;
  expected_signals: string[];
  listing: {
    submitted_url: string | null;
    listing_text: string;
    rent_cents: number;
    bedrooms: number;
    unit_type: string;
    address_text: string;
    city: string;
  };
  photos: { id: string; position: number; width: number; height: number }[];
  contacts: { kind: string; source: string }[];
  signals: SignalResult[];
  risk: Risk | null;
  related: Related | null;
};

export type FieldErrors = Record<string, string>;

export type SubmitResult =
  | { kind: "created"; checkId: string }
  | { kind: "invalid"; message: string; fields: FieldErrors }
  | { kind: "failed"; message: string };

type ErrorBody = {
  error: { code: string; message: string; details?: { fields?: { field: string; message: string }[] } };
};

function isErrorBody(value: unknown): value is ErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "message" in error;
}

export async function submitCheck(
  form: FormData,
  fetchImpl: typeof fetch = fetch,
): Promise<SubmitResult> {
  let response: Response;
  try {
    response = await fetchImpl(`${API_URL}/checks`, { method: "POST", body: form });
  } catch {
    return {
      kind: "failed",
      message: "We couldn't reach the checking service. Check your connection and try again.",
    };
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 202 && body && typeof body === "object" && "check_id" in body) {
    return { kind: "created", checkId: String((body as { check_id: unknown }).check_id) };
  }
  if (isErrorBody(body)) {
    const fields: FieldErrors = {};
    for (const f of body.error.details?.fields ?? []) {
      // "photos.2" (one photo) is shown next to the photo picker.
      const key = f.field.startsWith("photos.") ? "photos" : f.field;
      fields[key] = fields[key] ? `${fields[key]} ${f.message}` : f.message;
    }
    if (response.status === 422) return { kind: "invalid", message: body.error.message, fields };
    return { kind: "failed", message: body.error.message };
  }
  return { kind: "failed", message: `The service answered with HTTP ${response.status}.` };
}

export type FetchCheckResult =
  | { kind: "ok"; check: Check }
  | { kind: "not_found" }
  | { kind: "failed"; message: string };

export async function fetchCheck(
  id: string,
  fetchImpl: typeof fetch = fetch,
): Promise<FetchCheckResult> {
  let response: Response;
  try {
    response = await fetchImpl(`${API_URL}/checks/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
  } catch {
    return { kind: "failed", message: "We couldn't reach the checking service." };
  }
  if (response.status === 404 || response.status === 422) return { kind: "not_found" };
  if (!response.ok) return { kind: "failed", message: `HTTP ${response.status}` };
  return { kind: "ok", check: (await response.json()) as Check };
}
