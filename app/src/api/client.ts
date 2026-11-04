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

const isJson = (response: Response) => (response.headers.get("content-type") ?? "").includes("json");

async function request(path: string): Promise<Response> {
  try {
    return await fetch(`/api${path}`);
  } catch {
    throw new ApiError("Couldn’t reach the server", 0, "NETWORK");
  }
}

export async function getJson<T>(path: string): Promise<T> {
  let response = await request(path);

  // Development: an HTML page instead of JSON means the fake API worker went to sleep and
  // Vite answered with index.html. Wake the worker and ask once more.
  if (USE_MOCKS && !isJson(response)) {
    const { reactivateMocking } = await import("../mocks/browser");
    reactivateMocking();
    await new Promise((resolve) => setTimeout(resolve, 200));
    response = await request(path);
  }

  if (!isJson(response)) {
    throw new ApiError("The server sent a page instead of data", response.status, "NOT_JSON");
  }

  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as ApiErrorBody | null;
    throw new ApiError(
      body?.error?.message ?? `Request failed (${response.status})`,
      response.status,
      body?.error?.code,
    );
  }

  return (await response.json()) as T;
}
