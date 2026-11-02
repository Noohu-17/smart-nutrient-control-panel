// src/api/client.ts · the ONLY place that calls fetch. Screens use the hooks in hooks.ts.
import type { ApiErrorBody } from "./types";

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

  // Development: an HTML page instead of JSON means the fake API worker went to sleep and
  // Vite answered with index.html. Wake the worker and ask once more.
  if (import.meta.env.DEV && !isJson(response)) {
    const { reactivateMocking } = await import("../mocks/browser");
    reactivateMocking();
    await new Promise((resolve) => setTimeout(resolve, 200));
    response = await request(path, init);
  }

  if (!isJson(response)) {
    throw new ApiError("The server sent a page instead of data", response.status, "NOT_JSON");
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(
      body?.error?.message ?? `Request failed (${response.status})`,
      response.status,
      body?.error.code,
    );
  }

  return (await response.json()) as T;
}

export const getJson = <T>(path: string) => send<T>(path);

/** POST with a JSON body. Extra headers carry Idempotency-Key (§11.1). */
export const postJson = <T>(path: string, body?: unknown, headers: Record<string, string> = {}) =>
  send<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
