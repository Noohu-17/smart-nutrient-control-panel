// src/mocks/scenario.ts · scenario switcher for the fake API, so every screen state can be seen.
// Pick one by opening the app with ?mock=<name>, for example http://localhost:5173/overview?mock=z2-offline
// The choice is kept for the browser tab; ?mock=normal goes back.

export const SCENARIOS = {
  normal: "Both zones reporting, all sensors OK",
  "z2-offline": "Zone B controller not answering (503 DEVICE_OFFLINE)",
  "z2-sensor-fault": "Zone B EC probe faulty: ec is null, quality FAULT",
  "z2-stale": "Zone B last reading is 5 minutes old",
  "z1-estop": "E-STOP pressed on the Zone A dosing cabinet",
  "farm-error": "GET /api/farm fails (500)",
  "no-playground": "A real farm: features.playground is false, so no Simulator",
  "api-degraded": "Back end up, but database or broker down (503 DEGRADED)",
  slow: "Every answer takes 3 s, to see the loading states",
} as const;

export type ScenarioName = keyof typeof SCENARIOS;

const STORAGE_KEY = "snm.mockScenario";

function isScenario(value: string | null): value is ScenarioName {
  return value !== null && value in SCENARIOS;
}

/** Read on every request, so switching needs only a page reload. */
export function currentScenario(): ScenarioName {
  if (typeof window === "undefined") return "normal"; // tests in Node
  try {
    const fromUrl = new URLSearchParams(window.location.search).get("mock");
    if (isScenario(fromUrl)) {
      window.sessionStorage.setItem(STORAGE_KEY, fromUrl);
      return fromUrl;
    }
    const saved = window.sessionStorage.getItem(STORAGE_KEY);
    return isScenario(saved) ? saved : "normal";
  } catch {
    return "normal"; // storage blocked: fall back to the normal farm
  }
}
