import { useEffect, useId, useRef, useState } from "react";
import { usePushKey } from "../../api/hooks";
import { useSession } from "../../auth/session";
import type { Severity } from "./notify";
import { useNotifyPrefs } from "./prefs";
import { ApiError } from "../../api/client";
import {
  currentSubscription,
  pushOwner,
  PushProblem,
  pushProblemText,
  pushSeverities,
  pushSupported,
  subscribe,
  unsubscribe,
  updateSeverities,
} from "./push";

const CHOICES: { severity: Severity; label: string }[] = [
  { severity: "CRITICAL", label: "Critical" },
  { severity: "WARNING", label: "Warning" },
  { severity: "INFO", label: "Info" },
];

const supported = () => typeof Notification !== "undefined";

/** Notifications: which severities pop up for this person (critical always, so an E-STOP, a leak or
    a device offline is never missed; warning and info if they choose them), browser notifications
    while the tab is in the background, and notifications with the app closed (Web Push). The
    browser asks for permission only when they switch one of those on here, never when the app loads. */
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
      {user && <PushSetting severities={prefs.severities} userId={user.id} viewer={user.role === "Viewer"} />}
      <p className="dose-note">
        A critical alert pops up again every 5 minutes until someone acknowledges it. No SMS, e-mail or
        WhatsApp.
      </p>
    </section>
  );
}

/** Notifications with the app closed, on this device (Web Push, §11.2): critical alerts, and
    warnings if chosen above (info is never pushed). Not shown on a site that doesn't push. */
function PushSetting({
  severities,
  userId,
  viewer,
}: {
  severities: Severity[];
  userId: string;
  viewer: boolean;
}) {
  const key = usePushKey();
  const publicKey = key.data?.publicKey;
  const [on, setOn] = useState<boolean | null>(null); // null: still looking at this browser
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const ids = useId();
  const wanted = pushSeverities(severities).join();
  const sent = useRef<string | null>(null);

  // on only if this browser is subscribed and this person turned it on: on a shared device, someone
  // else's subscription shows as off until they turn it on themselves
  useEffect(() => {
    let live = true;
    currentSubscription().then(
      (subscription) => live && setOn(subscription !== null && pushOwner() === userId),
      () => live && setOn(false),
    );
    return () => {
      live = false;
    };
  }, [userId]);

  // Warning ticked or unticked above, or already subscribed when the page opened: the server is
  // told what this person picked, for this browser (made again if the server's key has changed)
  useEffect(() => {
    if (!on || !publicKey || sent.current === wanted) return;
    sent.current = wanted;
    updateSeverities(pushSeverities(severities), publicKey, userId).then(
      () => setProblem(""),
      (error: unknown) => {
        sent.current = null; // the next change tries again
        setProblem(
          error instanceof ApiError
            ? `Couldn’t save which alerts this device gets: ${error.message}`
            : error instanceof PushProblem
              ? error.message
              : "Couldn’t reach the server to save which alerts this device gets. Change a choice above to try again.",
        );
      },
    );
  }, [on, publicKey, wanted, severities, userId]);

  if (key.error?.status === 404) return null; // this site doesn't push alerts
  const why = !pushSupported()
    ? "This browser can’t get notifications with the app closed."
    : key.error?.status === 503
      ? "Notifications aren’t set up on the server yet."
      : "";

  async function switchPush(next: boolean) {
    setProblem("");
    setBusy(true);
    try {
      if (next) {
        await subscribe(pushSeverities(severities), userId);
        sent.current = wanted;
      } else {
        await unsubscribe();
        sent.current = null;
      }
      setOn(next);
    } catch (error) {
      setProblem(pushProblemText(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label className="sim-check">
        <input
          type="checkbox"
          checked={on === true}
          disabled={busy || on === null || key.isPending || Boolean(why)}
          aria-describedby={`${ids}-push`}
          onChange={(e) => void switchPush(e.target.checked)}
        />
        Notifications when the app is closed, on this device
      </label>
      <small id={`${ids}-push`} className="notify-always">
        {why ||
          (busy
            ? "Asking the browser and the server…"
            : "Critical alerts, and warnings if ticked above. Turn it off before you hand this device to someone else.")}
        {!why && !busy && viewer && (
          <>
            {" "}
            The farm’s alert settings say which roles get each alert; most go to Owners, Agronomists and
            Operators, so a Viewer may get few or none.
          </>
        )}
      </small>
      {problem && (
        <p className="banner warn" role="alert">
          {problem}
        </p>
      )}
    </>
  );
}
