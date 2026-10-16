// Calls the API's GET /health and turns every possible outcome into a typed status.
// Pure function with an injectable fetch so it can be unit tested without Next.js or a server.

// 127.0.0.1 rather than localhost: see docs/problems-and-solutions.md P-006.
export const DEFAULT_API_URL = "http://127.0.0.1:8100";
const TIMEOUT_MS = 5000;

export type HealthStatus =
  | { kind: "ok" }
  // The API answered but reported a problem (e.g. 503 database_unavailable).
  | { kind: "degraded"; httpStatus: number; code: string; message: string }
  // The API could not be reached at all (down, wrong URL, timeout).
  | { kind: "unreachable"; reason: string };

type ErrorBody = { error: { code: string; message: string } };

function isErrorBody(value: unknown): value is ErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { code?: unknown }).code === "string" &&
    typeof (error as { message?: unknown }).message === "string"
  );
}

export async function fetchHealth(
  apiUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<HealthStatus> {
  let response: Response;
  try {
    response = await fetchImpl(`${apiUrl}/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (err) {
    return { kind: "unreachable", reason: err instanceof Error ? err.message : String(err) };
  }

  if (response.ok) return { kind: "ok" };

  const body: unknown = await response.json().catch(() => null);
  if (isErrorBody(body)) {
    return {
      kind: "degraded",
      httpStatus: response.status,
      code: body.error.code,
      message: body.error.message,
    };
  }
  return {
    kind: "degraded",
    httpStatus: response.status,
    code: "unexpected_response",
    message: `API returned HTTP ${response.status}.`,
  };
}
