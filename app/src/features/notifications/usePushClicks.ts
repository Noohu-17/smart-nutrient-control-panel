import { useEffect } from "react";
import { useNavigate } from "react-router-dom";

/** A click on a pushed notification while the app is open: the service worker (public/push/sw.js)
    brings this window to the front and says which page to open, the alert's zone or Alerts. */
export function usePushClicks() {
  const navigate = useNavigate();
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    const workers = navigator.serviceWorker;
    const open = (event: MessageEvent) => {
      const message = event.data as { type?: string; page?: unknown } | null;
      // only the app's own pages: a path, never another site
      if (
        message?.type === "OPEN_ALERT" &&
        typeof message.page === "string" &&
        /^\/(?!\/)/.test(message.page)
      )
        navigate(message.page);
    };
    workers.addEventListener("message", open);
    return () => workers.removeEventListener("message", open);
  }, [navigate]);
}
