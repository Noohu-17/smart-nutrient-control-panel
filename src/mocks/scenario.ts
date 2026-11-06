// src/mocks/scenario.ts · scenario switcher for the fake API, so every screen state can be seen.
// Pick one by opening the app with ?mock=<name>, for example http://localhost:5173/overview?mock=z2-offline
// The choice is kept for the browser tab; ?mock=normal goes back.

export const SCENARIOS = {
  normal: "Both zones reporting, all sensors OK",
  "z2-offline": "Zone B controller silent: last values with online false",
  "z2-sensor-fault": "Zone B EC probe faulty: ec is null, quality FAULT",
  "z2-stale": "Zone B last reading is 5 minutes old",
  "z2-reservoir-low": "Zone B reservoir at 22 %, below the 30 % needed to dose",
  "z1-ec-stuck": "Zone A EC probe stuck: same value, quality STALE",
  "z1-estop": "E-STOP pressed on the Zone A dosing cabinet",
  "z1-leak": "Leak sensor wet in Zone A",
  "farm-error": "GET /api/farm fails (500)",
  "no-playground": "A real farm: features.playground is false, so no Simulator",
  "mains-lost": "Mains lost: the generator runs the farm, UPS on battery",
  "no-generator": "A site with no generator or UPS in its layout",
  "dose-blocked": "The controller refuses every dose (24 h limit)",
  "dose-failed": "The first dosing line runs with no flow",
  "dose-partial": "The second dosing line stops short",
  "dose-unknown": "The farm goes silent while a dose runs",
  "dose-expired": "Nothing accepts the dose before it expires",
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
