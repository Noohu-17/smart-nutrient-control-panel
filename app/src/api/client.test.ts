import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, deleteJson, getJson, postJson, setCsrfToken, setOnSignedOut } from "./client";

function answerWith(status: number, body: unknown) {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
}

describe("the session on writes (T1-35)", () => {
  afterEach(() => {
    setCsrfToken(null);
    setOnSignedOut(() => {});
    vi.restoreAllMocks();
  });

  const sentHeaders = () => new Headers(vi.mocked(globalThis.fetch).mock.calls[0][1]?.headers);

  it("sends the CSRF token on a POST, with the other headers kept, and never X-Demo-Role", async () => {
    answerWith(202, { ok: true });
    setCsrfToken("t1");
    await postJson("/stop", { scope: "ZONE", zone: "Z1" }, { "Idempotency-Key": "k1" });
    expect(sentHeaders().get("X-CSRF-Token")).toBe("t1");
    expect(sentHeaders().get("Idempotency-Key")).toBe("k1");
    expect(sentHeaders().has("X-Demo-Role")).toBe(false);
  });

  it("sends it on a DELETE too", async () => {
    answerWith(200, { ok: true });
    setCsrfToken("t2");
    await deleteJson("/sim/holds/Z1/ph");
    expect(sentHeaders().get("X-CSRF-Token")).toBe("t2");
  });

  it("sends no token when nobody is signed in", async () => {
    answerWith(202, { ok: true });
    setCsrfToken(null);
    await postJson("/stop", { scope: "ALL_OPERATIONS" });
    expect(sentHeaders().has("X-CSRF-Token")).toBe(false);
  });

  it("tells the session on a 401: it has ended", async () => {
    const signedOut = vi.fn();
    setOnSignedOut(signedOut);
    answerWith(401, { error: { code: "UNAUTHORIZED", message: "Sign in first" } });
    await expect(getJson("/zones")).rejects.toMatchObject({ status: 401, message: "Sign in first" });
    expect(signedOut).toHaveBeenCalledOnce();
  });

  it("takes a 204 with no body, as sign-out answers", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(null, { status: 204 }));
    await expect(postJson("/auth/logout")).resolves.toBeUndefined();
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
    expect(error).toMatchObject({
      message: "Line A exceeds 300 mL per dose",
      status: 409,
      code: "LIMIT_DOSE",
    });
  });

  it("reports a failed request when the body has no error field, like FastAPI's 404", async () => {
    answerWith(404, { detail: "Not Found" });

    const error = await getJson("/farm").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ message: "Request failed (404)", status: 404, code: undefined });
  });
});
