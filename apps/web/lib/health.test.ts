import { describe, expect, it } from "vitest";

import { fetchHealth } from "./health";

function fakeFetch(response: Response | Error): typeof fetch {
  return async () => {
    if (response instanceof Error) throw response;
    return response;
  };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("fetchHealth", () => {
  it("returns ok when the API answers 200", async () => {
    const status = await fetchHealth(
      "http://api",
      fakeFetch(json(200, { status: "ok", database: "ok" })),
    );
    expect(status).toEqual({ kind: "ok" });
  });

  it("returns degraded with the API's error code when the database is down", async () => {
    const body = {
      error: {
        code: "database_unavailable",
        message: "The database is not reachable.",
        details: { database: "unavailable" },
      },
    };
    const status = await fetchHealth("http://api", fakeFetch(json(503, body)));
    expect(status).toEqual({
      kind: "degraded",
      httpStatus: 503,
      code: "database_unavailable",
      message: "The database is not reachable.",
    });
  });

  it("returns degraded with a generic code when the error body is not the documented shape", async () => {
    const status = await fetchHealth(
      "http://api",
      fakeFetch(new Response("Bad Gateway", { status: 502 })),
    );
    expect(status).toMatchObject({ kind: "degraded", httpStatus: 502, code: "unexpected_response" });
  });

  it("returns unreachable when the request itself fails", async () => {
    const status = await fetchHealth("http://api", fakeFetch(new TypeError("fetch failed")));
    expect(status).toEqual({ kind: "unreachable", reason: "fetch failed" });
  });
});
