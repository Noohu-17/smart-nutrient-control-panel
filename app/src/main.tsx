import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { USE_MOCKS } from "./api/client";
import { makeQueryClient } from "./app/queryClient";
import { routes } from "./app/routes";
import { SessionProvider } from "./auth/SessionProvider";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/cards.css";
import "./styles/dosing.css";
import "./styles/sim.css";

const router = createBrowserRouter(routes);
const queryClient = makeQueryClient();

/** T2-4 · start the fake API (MSW) before the first request. Development only, and off with
    VITE_USE_MOCKS=false (T2-12), when /api goes to the real back end. */
async function enableMocking() {
  if (!USE_MOCKS) return;
  const { worker, reactivateMocking } = await import("./mocks/browser");
  // 'bypass': let Vite modules, fonts and images through without a warning; only /api/* is faked
  await worker.start({ onUnhandledFrame: "bypass" });
  // A hidden tab lets the browser put the service worker to sleep; re-attach when the tab is back.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") reactivateMocking();
  });
}

void enableMocking().then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <SessionProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>
      </SessionProvider>
    </StrictMode>,
  );
});
