import { ApiError } from "../api/client";
import { useHealth } from "../api/hooks";

type Look = { tone: "off" | "ok" | "warn" | "crit"; text: string; why: string };

/** Connection chip in the top bar, from GET /api/health every 30 s: can the app reach the server,
    and can the server reach its database and the farm's broker? */
export function LiveChip() {
  const health = useHealth();
  const look: Look = health.isPending
    ? { tone: "off", text: "Connecting…", why: "Checking the server" }
    : health.isSuccess
      ? { tone: "ok", text: "Connected", why: "The server, its database and the farm link are all up" }
      : health.error instanceof ApiError && health.error.status === 503
        ? { tone: "warn", text: "Server problem", why: "The server is up, but its database or the farm link is down" }
        : { tone: "crit", text: "Not connected", why: "The app can’t reach the server, so what you see may be old" };
  return (
    <span className="live-chip" data-tone={look.tone} title={look.why}>
      <i />
      {look.text}
    </span>
  );
}
