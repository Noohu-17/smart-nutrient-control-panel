import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { routes } from "./app/routes";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/layout.css";

const router = createBrowserRouter(routes);
const queryClient = new QueryClient();

/** T2-4 · start the fake API (MSW) before the first request. Development only. */
async function enableMocking() {
  if (!import.meta.env.DEV) return;
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
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
});
