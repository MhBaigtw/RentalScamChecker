import { describe, expect, it } from "vitest";

import { fetchCheck, submitCheck } from "./api";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function fakeFetch(response: Response | Error): typeof fetch {
  return async () => {
    if (response instanceof Error) throw response;
    return response;
  };
}

describe("submitCheck", () => {
  it("returns the new check id on 202", async () => {
    const result = await submitCheck(new FormData(), fakeFetch(json(202, { check_id: "abc" })));
    expect(result).toEqual({ kind: "created", checkId: "abc" });
  });

  it("maps field errors, folding per-photo errors into one photos entry", async () => {
    const body = {
      error: {
        code: "validation_error",
        message: "Some fields are missing or invalid.",
        details: {
          fields: [
            { field: "monthly_rent", message: "This field is required." },
            { field: "photos.0", message: "Photo 1: the file is not an image." },
            { field: "photos.2", message: "Photo 3: the file is larger than 10 MB." },
          ],
        },
      },
    };
    const result = await submitCheck(new FormData(), fakeFetch(json(422, body)));
    expect(result).toEqual({
      kind: "invalid",
      message: "Some fields are missing or invalid.",
      fields: {
        monthly_rent: "This field is required.",
        photos: "Photo 1: the file is not an image. Photo 3: the file is larger than 10 MB.",
      },
    });
  });

  it("reports an unreachable service plainly", async () => {
    const result = await submitCheck(new FormData(), fakeFetch(new TypeError("fetch failed")));
    expect(result.kind).toBe("failed");
  });
});

describe("fetchCheck", () => {
  it("treats 404 and an invalid id (422) as not found", async () => {
    expect(await fetchCheck("x", fakeFetch(json(404, {})))).toEqual({ kind: "not_found" });
    expect(await fetchCheck("x", fakeFetch(json(422, {})))).toEqual({ kind: "not_found" });
  });

  it("returns the check on 200", async () => {
    const result = await fetchCheck("x", fakeFetch(json(200, { id: "x", status: "pending" })));
    expect(result).toMatchObject({ kind: "ok", check: { id: "x", status: "pending" } });
  });
});
