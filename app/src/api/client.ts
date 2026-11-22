// src/api/client.ts · the ONLY place that calls fetch. Screens use the hooks in hooks.ts.
import type { ApiErrorBody } from "./types";

/** Development answers from the fake API (MSW) unless VITE_USE_MOCKS=false; then Vite's proxy
    sends /api to the real back end (vite.config.ts). Production never uses the fake API. */
export const USE_MOCKS = import.meta.env.DEV && import.meta.env.VITE_USE_MOCKS !== "false";

export class ApiError extends Error {
  status: number;
  code: string | undefined;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

// The session itself is an HttpOnly cookie the browser sends (T1-35); the app never sees it. What
// the app keeps is the CSRF token from sign-in or GET /me, sent on every request that isn't a GET.
let csrfToken: string | null = null;
let onSignedOut: () => void = () => {};

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

/** Called on any 401: the session has ended (expired, signed out elsewhere, a new role). */
export function setOnSignedOut(listener: () => void) {
  onSignedOut = listener;
}

const writeHeaders = (): Record<string, string> => (csrfToken ? { "X-CSRF-Token": csrfToken } : {});

const isJson = (response: Response) => (response.headers.get("content-type") ?? "").includes("json");

async function request(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`/api${path}`, init);
  } catch {
    throw new ApiError("Couldn’t reach the server", 0, "NETWORK");
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  let response = await request(path, init);
  if (response.status === 204) return undefined as T; // sign-out answers with no body

  // Development: an HTML page instead of JSON means the fake API worker went to sleep and
  // Vite answered with index.html. Wake the worker and ask once more.
  if (USE_MOCKS && !isJson(response)) {
    const { reactivateMocking } = await import("../mocks/browser");
    reactivateMocking();
    await new Promise((resolve) => setTimeout(resolve, 200));
    response = await request(path, init);
  }

  if (!isJson(response)) {
    throw new ApiError("The server sent a page instead of data", response.status, "NOT_JSON");
  }

  if (!response.ok) {
    if (response.status === 401) onSignedOut();
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(
      body?.error?.message ?? `Request failed (${response.status})`,
      response.status,
      body?.error?.code,
    );
  }

  return (await response.json()) as T;
}

export const getJson = <T>(path: string) => send<T>(path);

/** POST with a JSON body. Extra headers carry Idempotency-Key (§11.1). */
export const postJson = <T>(path: string, body?: unknown, headers: Record<string, string> = {}) =>
  send<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...writeHeaders(), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

/** DELETE, with a JSON body when the route takes one (DELETE /push/subscriptions { endpoint }). */
export const deleteJson = <T>(path: string, body?: unknown) =>
  send<T>(
    path,
    body === undefined
      ? { method: "DELETE", headers: writeHeaders() }
      : {
          method: "DELETE",
          headers: { "Content-Type": "application/json", ...writeHeaders() },
          body: JSON.stringify(body),
        },
  );
