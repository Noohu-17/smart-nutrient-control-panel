// src/test/setup.ts · runs before every test file (vite.config.ts → test.setupFiles)
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { resetTelemetry } from "../mocks/data/telemetry";
import { server } from "../mocks/node";

beforeAll(() => {
  server.listen({ onUnhandledFrame: "error" });
  // The app calls fetch('/api/...'). Node's fetch needs a full URL, so resolve it against the page.
  const mswFetch = globalThis.fetch;
  globalThis.fetch = (input, init) =>
    mswFetch(
      typeof input === "string" && input.startsWith("/") ? new URL(input, window.location.origin) : input,
      init,
    );
});

afterEach(() => {
  cleanup();
  server.resetHandlers();
  resetTelemetry();
});

afterAll(() => server.close());
