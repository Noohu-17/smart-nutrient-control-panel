import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, deleteJson, getJson, postJson, setDemoRole } from "./client";

function answerWith(status: number, body: unknown) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

describe("the demo role on writes", () => {
  afterEach(() => {
    setDemoRole(null);
    vi.restoreAllMocks();
  });

  const sentHeaders = () => new Headers(vi.mocked(globalThis.fetch).mock.calls[0][1]?.headers);

  it("sends the signed-in role as X-Demo-Role on a POST, with the other headers kept", async () => {
    answerWith(202, { ok: true });
    setDemoRole("Operator");
    await postJson("/stop", { scope: "ZONE", zone: "Z1" }, { "Idempotency-Key": "k1" });
    expect(sentHeaders().get("X-Demo-Role")).toBe("Operator");
    expect(sentHeaders().get("Idempotency-Key")).toBe("k1");
  });

  it("sends it on a DELETE too", async () => {
    answerWith(200, { ok: true });
    setDemoRole("Owner");
    await deleteJson("/sim/holds/Z1/ph");
    expect(sentHeaders().get("X-Demo-Role")).toBe("Owner");
  });

  it("sends no role when nobody is signed in", async () => {
    answerWith(202, { ok: true });
    await postJson("/stop", { scope: "ALL_OPERATIONS" });
    expect(sentHeaders().has("X-Demo-Role")).toBe(false);
  });
});

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
