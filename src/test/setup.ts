// src/test/setup.ts · runs before every test file (vite.config.ts → test.setupFiles)
import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { resetCommands } from "../mocks/commands";
import { resetClimatePower } from "../mocks/data/climatePower";
import { resetTelemetry } from "../mocks/data/telemetry";
import { server } from "../mocks/node";
import { resetSim } from "../mocks/sim";

// The fake API alone takes 300 ms, which left little of the default 1 s on a busy laptop.
configure({ asyncUtilTimeout: 5_000 });

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
  resetClimatePower();
  resetSim();
  resetCommands();
});

afterAll(() => server.close());
