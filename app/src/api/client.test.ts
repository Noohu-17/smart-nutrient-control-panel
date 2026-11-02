import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, getJson } from "./client";

function answerWith(status: number, body: unknown) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

describe("getJson errors", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reads the contract's error body", async () => {
    answerWith(409, { error: { code: "LIMIT_DOSE", message: "Line A exceeds 300 mL per dose" } });

    const error = await getJson("/zones/Z1").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: "Line A exceeds 300 mL per dose", status: 409, code: "LIMIT_DOSE" });
  });

  it("reports a failed request when the body has no error field, like FastAPI's 404", async () => {
    answerWith(404, { detail: "Not Found" });

    const error = await getJson("/farm").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: "Request failed (404)", status: 404, code: undefined });
  });
});
