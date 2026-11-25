// src/mocks/session.test.ts · T2-27 · the fake API's sign-in answers as T1-35 does
// (backend/app/auth/access.py and README.md), so the app is tested against the same rules
import { describe, expect, it } from "vitest";
import type { ApiErrorBody, Me } from "../api/types";
import { csrfHeaders, FAKE_PASSWORD, fakeSignIn } from "./session";

const post = (path: string, body: unknown = {}, headers: Record<string, string> = csrfHeaders()) =>
  fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
const errorOf = async (res: Response) => ((await res.json()) as ApiErrorBody).error;
const DOSE = { zone: "Z1", lines: [{ line: "A", ml: 10 }] };

describe("fake sign-in (T1-35)", () => {
  it("answers 401 to every route without a session, except health and sign-in", async () => {
    fakeSignIn(null);
    expect((await fetch("/api/zones")).status).toBe(401);
    expect(await errorOf(await fetch("/api/me"))).toEqual({ code: "UNAUTHORIZED", message: "Sign in first" });
    expect((await post("/stop", { scope: "ALL_OPERATIONS" })).status).toBe(401);
    expect((await fetch("/api/health")).status).toBe(200);
  });

  it("signs in with a password and answers as GET /api/me does", async () => {
    fakeSignIn(null);
    const res = await post("/auth/login", { email: "Operator@farm.test ", password: FAKE_PASSWORD });
    const me = (await res.json()) as Me;
    expect(me).toMatchObject({ user: { name: "Ravi Operator", role: "OPERATOR" }, step: "DONE" });
    expect(Object.keys(me).sort()).toEqual(["csrfToken", "expiresAt", "step", "user"]);
    expect(await (await fetch("/api/me")).json()).toMatchObject({ user: me.user, csrfToken: me.csrfToken });
  });

  it("refuses a demo user's email at the password sign-in: they have no password", async () => {
    fakeSignIn(null);
    expect(
      (await post("/auth/login", { email: "operator@example.com", password: FAKE_PASSWORD })).status,
    ).toBe(401);
  });

  it("needs the CSRF token on every write", async () => {
    fakeSignIn("OPERATOR");
    const res = await post("/stop", { scope: "ALL_OPERATIONS" }, {});
    expect(res.status).toBe(403);
    expect((await errorOf(res)).code).toBe("FORBIDDEN");
  });

  it("checks the role: a Viewer can't dose but can Stop, and only an Owner resets the farm", async () => {
    fakeSignIn("VIEWER");
    const dose = await post("/dose-requests", DOSE);
    expect(dose.status).toBe(403);
    expect(await errorOf(dose)).toEqual({ code: "FORBIDDEN", message: "Viewers can’t dose" });
    expect((await post("/stop", { scope: "ALL_OPERATIONS" })).status).toBe(202);

    fakeSignIn("OPERATOR");
    expect((await post("/dose-requests", DOSE)).status).not.toBe(403);
    expect(await errorOf(await post("/sim/reset"))).toEqual({
      code: "FORBIDDEN",
      message: "Only an Owner can reset the simulated farm",
    });
  });

  it("lets a session waiting for two-factor reach only the code, /me and sign-out", async () => {
    fakeSignIn(null);
    const me = (await (
      await post("/auth/login", { email: "owner@farm.test", password: FAKE_PASSWORD })
    ).json()) as Me;
    expect(me.step).toBe("TOTP");
    const headers = { "X-CSRF-Token": me.csrfToken };
    expect((await errorOf(await post("/stop", { scope: "ALL_OPERATIONS" }, headers))).code).toBe(
      "TWO_FACTOR_REQUIRED",
    );
    expect((await fetch("/api/me")).status).toBe(200);
    expect((await post("/auth/logout", undefined, headers)).status).toBe(204);
  });
});
