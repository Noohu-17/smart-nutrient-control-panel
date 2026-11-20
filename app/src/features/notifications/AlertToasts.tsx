import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAckAlert, useAlerts, useFarm } from "../../api/hooks";
import type { ServerAlert } from "../../api/types";
import { useSession } from "../../auth/session";
import { RoleButton } from "../../components/RoleButton";
import { zonePath } from "../../config/readings";
import { dueNow, MAX_TOASTS, openSince, toastOrder, WARNING_FADE_MS } from "./notify";
import { useNotifyPrefs } from "./prefs";

const SEVERITY_TEXT = { CRITICAL: "Critical", WARNING: "Warning", INFO: "Info" } as const;
const TONE = { CRITICAL: "crit", WARNING: "warn", INFO: "info" } as const;
// how often to look for repeats due when the alerts haven't changed
const CHECK_MS = 30_000;

const where = (alert: ServerAlert) => (alert.zone ? zonePath(alert.zone) : "/alerts");

/** A browser notification, if this person switched them on and the browser allows it. Its tag is
    the alert's, so a repeat replaces the one before. A click brings the app back at the zone. */
function notifyBrowser(alert: ServerAlert, title: string, open: () => void) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const shown = new Notification(title, {
    body: alert.nextStep,
    tag: alert.id,
    renotify: true,
  } as NotificationOptions);
  shown.onclick = () => {
    window.focus();
    open();
    shown.close();
  };
}

function Toast({
  alert,
  title,
  onOpen,
  onDismiss,
}: {
  alert: ServerAlert;
  title: string;
  onOpen: () => void;
  onDismiss: () => void;
}) {
  const ack = useAckAlert();
  // a warning fades; a critical alert stays until dismissed or acknowledged. The timer starts
  // once per toast: the app re-renders on every poll, which mustn't restart it.
  const dismissRef = useRef(onDismiss);
  useEffect(() => {
    dismissRef.current = onDismiss;
  });
  useEffect(() => {
    if (alert.severity === "CRITICAL") return;
    const timer = setTimeout(() => dismissRef.current(), WARNING_FADE_MS);
    return () => clearTimeout(timer);
  }, [alert.id, alert.severity]);

  return (
    <li>
      <div
        className={`toast ${TONE[alert.severity]}`}
        role={alert.severity === "CRITICAL" ? "alert" : "status"}
      >
        <button type="button" className="toast-body" onClick={onOpen}>
          <b>{title}</b>
          <span>{alert.nextStep}</span>
        </button>
        <div className="toast-actions">
          <RoleButton perm="ack" onClick={() => ack.mutate(alert.id)}>
            {ack.isPending ? "Acknowledging…" : "Acknowledge"}
          </RoleButton>
          <button type="button" className="toast-close" aria-label={`Dismiss: ${title}`} onClick={onDismiss}>
            ×
          </button>
        </div>
        {ack.isError && <small role="alert">Not acknowledged: {ack.error.message}</small>}
      </div>
    </li>
  );
}

/** A toast in the corner for each new alert while the app is open (T2-22's alerts), at most
    MAX_TOASTS at once, and a browser notification for it while the tab is in the background.
    Which severities notify is each person's choice (Alerts page, Notifications). */
export function AlertToasts() {
  const { user } = useSession();
  const [prefs] = useNotifyPrefs(user?.id ?? null);
  const alerts = useAlerts();
  const farm = useFarm();
  const navigate = useNavigate();
  const last = useRef<Map<string, number> | null>(null);
  const [toasts, setToasts] = useState<ServerAlert[]>([]);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), CHECK_MS);
    return () => clearInterval(timer);
  }, []);

  const names = new Map((farm.data?.zones ?? []).map((zone) => [zone.id, zone.name]));
  const title = (alert: ServerAlert) =>
    `${SEVERITY_TEXT[alert.severity]}: ${alert.zone ? `${names.get(alert.zone) ?? alert.zone} · ` : ""}${alert.message}`;
  const titleRef = useRef(title);
  useEffect(() => {
    titleRef.current = title;
  });

  useEffect(() => {
    const all = alerts.data;
    if (!all) return;
    const now = Date.now();
    if (last.current === null) {
      last.current = openSince(all, now);
      return;
    }
    const due = dueNow(all, prefs.severities, last.current, now);
    const latest = new Map(all.map((a) => [a.id, a]));
    setToasts((shown) => {
      // an acknowledged or cleared alert leaves; the others take the server's newest words
      const kept = shown.flatMap((t) => (latest.get(t.id)?.status === "OPEN" ? [latest.get(t.id)!] : []));
      const fresh = due.filter((a) => !kept.some((t) => t.id === a.id));
      return [...fresh, ...kept].sort(toastOrder);
    });
    if (prefs.browser && document.visibilityState === "hidden")
      for (const alert of due) notifyBrowser(alert, titleRef.current(alert), () => navigate(where(alert)));
  }, [alerts.data, prefs, tick, navigate]);

  const dismiss = (id: string) => setToasts((shown) => shown.filter((t) => t.id !== id));
  const more = toasts.length - MAX_TOASTS;
  if (toasts.length === 0) return null;
  return (
    <section className="toasts" aria-label="New alerts">
      <ul>
        {toasts.slice(0, MAX_TOASTS).map((alert) => (
          <Toast
            key={alert.id}
            alert={alert}
            title={title(alert)}
            onOpen={() => {
              navigate(where(alert));
              dismiss(alert.id);
            }}
            onDismiss={() => dismiss(alert.id)}
          />
        ))}
      </ul>
      {more > 0 && (
        <Link className="toast-more" to="/alerts">
          +{more} more
        </Link>
      )}
    </section>
  );
}
