// src/mocks/browser.ts · starts the MSW worker (used by main.tsx and api/client.ts in development only)
import { setupWorker } from "msw/browser";
import { handlers } from "./handlers";

export const worker = setupWorker(...handlers);

/** Tell a restarted service worker to answer for this page again (same message worker.start() sends). */
export function reactivateMocking() {
  navigator.serviceWorker?.controller?.postMessage("MOCK_ACTIVATE");
}
