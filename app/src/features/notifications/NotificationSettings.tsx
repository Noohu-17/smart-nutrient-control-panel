import { useId, useState } from "react";
import { useSession } from "../../auth/session";
import type { Severity } from "./notify";
import { useNotifyPrefs } from "./prefs";

const CHOICES: { severity: Severity; label: string }[] = [
  { severity: "CRITICAL", label: "Critical" },
  { severity: "WARNING", label: "Warning" },
  { severity: "INFO", label: "Info" },
];

const supported = () => typeof Notification !== "undefined";

/** Notifications: which severities pop up for this person (critical always, so an E-STOP, a leak or
    a device offline is never missed; warning and info if they choose them), and browser notifications while the tab is in the background. The browser asks for permission
    only when they switch those on here, never when the app loads. In the app only. */
export function NotificationSettings() {
  const { user } = useSession();
  const [prefs, update] = useNotifyPrefs(user?.id ?? null);
  const [problem, setProblem] = useState("");
  const ids = useId();

  function toggle(severity: Severity, on: boolean) {
    const rest = prefs.severities.filter((s) => s !== severity);
    update({ severities: on ? [...rest, severity] : rest });
  }

  async function switchBrowser(on: boolean) {
    setProblem("");
    if (!on) return update({ browser: false });
    if (!supported()) return setProblem("This browser can’t show notifications.");
    const permission =
      Notification.permission === "default"
        ? await Notification.requestPermission()
        : Notification.permission;
    if (permission === "granted") update({ browser: true });
    else setProblem("Notifications are blocked for this site. Allow them in the browser’s site settings.");
  }

  return (
    <section className="zone-card" aria-labelledby={`${ids}-title`}>
      <header className="zone-card-head">
        <h2 id={`${ids}-title`}>Notifications</h2>
      </header>
      <fieldset className="notify-choices">
        <legend>Pop up for</legend>
        {CHOICES.map(({ severity, label }) => (
          <label key={severity} className="sim-check">
            <input
              type="checkbox"
              checked={severity === "CRITICAL" || prefs.severities.includes(severity)}
              disabled={severity === "CRITICAL"}
              aria-describedby={severity === "CRITICAL" ? `${ids}-critical` : undefined}
              onChange={(e) => toggle(severity, e.target.checked)}
            />
            {label}
          </label>
        ))}
        <small id={`${ids}-critical`} className="notify-always">
          Critical alerts always pop up: an E-STOP, a leak or a device offline mustn’t be missed.
        </small>
      </fieldset>
      <label className="sim-check">
        <input
          type="checkbox"
          checked={prefs.browser && supported() && Notification.permission === "granted"}
          onChange={(e) => void switchBrowser(e.target.checked)}
        />
        Browser notifications while this tab is in the background
      </label>
      {problem && (
        <p className="banner warn" role="alert">
          {problem}
        </p>
      )}
      <p className="dose-note">
        A critical alert pops up again every 5 minutes until someone acknowledges it. In the app only: no SMS,
        e-mail or WhatsApp.
      </p>
    </section>
  );
}
