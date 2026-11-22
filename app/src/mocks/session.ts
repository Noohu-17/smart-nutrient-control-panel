// src/mocks/session.ts · T2-27 · the fake API's sign-in, in the shapes of the real one (T1-35,
// backend/app/auth/README.md): one session per browser, the CSRF token, two-factor for the Owner
// and the role checks of access.py. Development only: no real password ever goes here.
import { HttpResponse } from "msw";
import type { ApiErrorBody, Me, ServerRole, TotpSetup } from "../api/types";

type FakeUser = {
  id: string;
  name: string;
  email: string;
  role: ServerRole;
  password: string | null; // null: a demo user, who signs in only with the role picker
  twoFactor: boolean;
};

type FakeSession = { user: FakeUser; step: Me["step"]; csrfToken: string; wrongCodes: number };

/** Every fake account's password, and the code the fake authenticator always shows. */
export const FAKE_PASSWORD = "fake-password";
export const FAKE_CODE = "123456";
const MAX_WRONG_CODES = 5;
const SESSION_HOURS = 12;

const demo = (role: ServerRole, name: string): FakeUser => ({
  id: `demo-${role.toLowerCase()}`,
  name: `Demo ${name}`,
  email: `${role.toLowerCase()}@example.com`,
  role,
  password: null,
  twoFactor: false,
});

// The demo users of config/clients/client_A/users.json, then accounts with a password
const USERS: FakeUser[] = [
  demo("OWNER", "Owner"),
  demo("AGRONOMIST", "Agronomist"),
  demo("OPERATOR", "Operator"),
  demo("VIEWER", "Viewer"),
  {
    id: "u-owner",
    name: "Farm Owner",
    email: "owner@farm.test",
    role: "OWNER",
    password: FAKE_PASSWORD,
    twoFactor: true,
  },
  {
    id: "u-new-owner",
    name: "New Owner",
    email: "new-owner@farm.test",
    role: "OWNER",
    password: FAKE_PASSWORD,
    twoFactor: false,
  },
  {
    id: "u-agronomist",
    name: "Asha Agronomist",
    email: "agronomist@farm.test",
    role: "AGRONOMIST",
    password: FAKE_PASSWORD,
    twoFactor: false,
  },
  {
    id: "u-operator",
    name: "Ravi Operator",
    email: "operator@farm.test",
    role: "OPERATOR",
    password: FAKE_PASSWORD,
    twoFactor: false,
  },
  {
    id: "u-viewer",
    name: "Vani Viewer",
    email: "viewer@farm.test",
    role: "VIEWER",
    password: FAKE_PASSWORD,
    twoFactor: false,
  },
];

// Kept for the tab, as the real cookie is, so a page reload doesn't sign the developer out
const STORE_KEY = "snm.fakeSession";
let session: FakeSession | null = load();
const twoFactorSetUp = new Set<string>(); // Owners who set up two-factor since the page loaded

/** Tests: signed out, and no one has set up two-factor. */
export function resetSession() {
  save(null);
  twoFactorSetUp.clear();
}

function load(): FakeSession | null {
  try {
    return JSON.parse(sessionStorage.getItem(STORE_KEY) ?? "null") as FakeSession | null;
  } catch {
    return null;
  }
}

function save(next: FakeSession | null) {
  session = next;
  try {
    if (next) sessionStorage.setItem(STORE_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(STORE_KEY);
  } catch {
    /* storage blocked: the session lasts until the page reloads */
  }
}

const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json<ApiErrorBody>({ error: { code, message } }, { status });

const me = (s: FakeSession): Me => ({
  user: { id: s.user.id, name: s.user.name, email: s.user.email, role: s.user.role },
  csrfToken: s.csrfToken,
  expiresAt: new Date(Date.now() + SESSION_HOURS * 3_600_000).toISOString(),
  step: s.step,
});

/** A new session, as each sign-in and each right code starts one. */
function start(user: FakeUser, step: Me["step"]): Me {
  save({ user, step, csrfToken: crypto.randomUUID(), wrongCodes: 0 });
  return me(session!);
}

/** The signed-in user's name, for "Acknowledged by …". */
export const signedInName = () => session?.user.name ?? null;

/** Who is signed in, as GET /api/operations names them in `by` (T2-23). */
export const signedInUser = () =>
  session ? { id: session.user.id, name: session.user.name, role: session.user.role } : null;

/** A demo user (picked from the role picker), whom everyone who picks that role shares. */
export const signedInIsDemo = () => session?.user.password === null;

/** Tests: sign in as an account with a password, by its ID ("u-owner", "u-agronomist" …). */
export function fakeSignInAccount(id: string): Me {
  return start(
    USERS.find((u) => u.id === id && u.password !== null)!,
    "DONE",
  );
}

/** Tests: sign in as that role's demo user, as renderAt does, or sign out (null). */
export function fakeSignIn(role: ServerRole | null): Me | null {
  if (!role) {
    save(null);
    return null;
  }
  return start(
    USERS.find((u) => u.role === role && u.password === null)!,
    "DONE",
  );
}

/* ---------- the routes (handlers.ts) ---------- */

export function login(body: { email?: string; password?: string }) {
  const email = body.email?.trim().toLowerCase();
  const user = USERS.find((u) => u.email === email && u.password !== null);
  if (!user || body.password !== user.password)
    return apiError(401, "UNAUTHORIZED", "Wrong email or password");
  const hasTwoFactor = user.twoFactor || twoFactorSetUp.has(user.id);
  const step = hasTwoFactor ? "TOTP" : user.role === "OWNER" ? "TOTP_SETUP" : "DONE";
  return HttpResponse.json(start(user, step));
}

export function demoLogin(body: { role?: string }) {
  const user = USERS.find((u) => u.role === body.role && u.password === null);
  if (!user) return apiError(422, "VALIDATION_ERROR", "role must be OWNER, AGRONOMIST, OPERATOR or VIEWER");
  return HttpResponse.json(start(user, "DONE"));
}

export function twoFactor(body: { code?: string }) {
  if (!session) return apiError(401, "UNAUTHORIZED", "Sign in first");
  if (session.step === "DONE")
    return apiError(409, "CONFLICT", "Two-factor is already done for this session");
  if (session.step === "TOTP_SETUP" && body.code === undefined) {
    const secret = "JBSWY3DPEHPK3PXP"; // a made-up one; the fake accepts FAKE_CODE whatever the secret
    const answer: TotpSetup = {
      secret,
      otpauthUri: `otpauth://totp/Smart%20Nutrient:${encodeURIComponent(session.user.email)}?secret=${secret}`,
    };
    return HttpResponse.json(answer);
  }
  if (body.code === undefined)
    return apiError(422, "VALIDATION_ERROR", "Send the 6-digit code from the authenticator app");
  if (body.code !== FAKE_CODE) {
    save({ ...session, wrongCodes: session.wrongCodes + 1 });
    if (session.wrongCodes >= MAX_WRONG_CODES) {
      save(null);
      return apiError(401, "UNAUTHORIZED", "Too many wrong codes: sign in again");
    }
    return apiError(400, "WRONG_CODE", "That code is not right. Check the time on the phone and try again");
  }
  twoFactorSetUp.add(session.user.id);
  return HttpResponse.json(start(session.user, "DONE"));
}

export function signOut() {
  save(null);
  return new HttpResponse(null, { status: 204 });
}

export const whoAmI = () =>
  session ? HttpResponse.json(me(session)) : apiError(401, "UNAUTHORIZED", "Sign in first");

/* ---------- the gate (access.py): before every other route ---------- */

const OPEN = new Set(["/api/health", "/api/auth/login", "/api/auth/demo"]);
const BEFORE_TWO_FACTOR = new Set(["/api/auth/totp", "/api/me", "/api/auth/logout"]);
const OWNER_ONLY: [RegExp, string][] = [
  [/^\/api\/sim\/reset$/, "Only an Owner can reset the simulated farm"],
  [/^\/api\/users(\/|$)/, "Only an Owner manages users"],
];
// Stop: every role (§13). Web Push: everyone signed in (§11.2), demo users get 403 from the route itself
const ANY_ROLE = new Set([
  "/api/stop",
  ...BEFORE_TWO_FACTOR,
  "/api/auth/logout-all",
  "/api/push/subscriptions",
]);

/** Why a Viewer can't, in the server's words. */
function viewerReason(path: string) {
  if (path.startsWith("/api/dose-requests")) return "Viewers can’t dose";
  if (path.startsWith("/api/alerts")) return "Viewers can’t acknowledge alerts";
  if (path.startsWith("/api/sim")) return "Viewers can’t change the simulated farm";
  if (path === "/api/resume") return "Viewers can’t resume";
  return "Viewers can’t do this";
}

/** 401, 403, or nothing: then the route answers. */
export function gate(request: Request) {
  const path = new URL(request.url).pathname;
  if (OPEN.has(path)) return undefined;
  if (!session) return apiError(401, "UNAUTHORIZED", "Sign in first");
  if (session.step !== "DONE" && !BEFORE_TWO_FACTOR.has(path))
    return apiError(403, "TWO_FACTOR_REQUIRED", "Finish signing in with your authenticator app first");
  if (request.method === "GET") return undefined;
  if (request.headers.get("X-CSRF-Token") !== session.csrfToken)
    return apiError(403, "FORBIDDEN", "The X-CSRF-Token header is missing or wrong. GET /api/me gives it");
  if (ANY_ROLE.has(path)) return undefined;
  const ownerOnly = OWNER_ONLY.find(([pattern]) => pattern.test(path));
  if (ownerOnly && session.user.role !== "OWNER") return apiError(403, "FORBIDDEN", ownerOnly[1]);
  if (session.user.role === "VIEWER") return apiError(403, "FORBIDDEN", viewerReason(path));
  return undefined;
}

/** The headers a test's own fetch needs for a write, as the app's client sends them. */
export const csrfHeaders = (): Record<string, string> =>
  session ? { "X-CSRF-Token": session.csrfToken } : {};
