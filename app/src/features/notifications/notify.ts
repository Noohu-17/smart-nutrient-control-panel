import type { ServerAlert } from "../../api/types";

/* When an alert notifies (toast in the app, and a browser notification while the tab is in the
   background). In the app only: no SMS, e-mail or WhatsApp in this phase. */

/** A warning's toast fades after this; a critical one stays until dismissed. */
export const WARNING_FADE_MS = 6000;
/** A critical alert nobody has acknowledged notifies again this often. Acknowledging it, in any
    browser, stops the repeats everywhere: the server's status changes and every app sees it. */
export const REPEAT_MS = 5 * 60_000;
/** Toasts shown at once; the rest are "+N more". */
export const MAX_TOASTS = 3;

export type Severity = ServerAlert["severity"];

/** Which alerts notify now, given when each was last notified (`last`, updated here):
    a new open alert of a chosen severity, or a critical one still open after REPEAT_MS.
    An alert that is no longer open is forgotten, so it notifies again if it comes back. */
export function dueNow(
  alerts: ServerAlert[],
  severities: Severity[],
  last: Map<string, number>,
  now: number,
): ServerAlert[] {
  const open = new Set(alerts.filter((a) => a.status === "OPEN").map((a) => a.id));
  for (const id of last.keys()) if (!open.has(id)) last.delete(id);

  const due = alerts.filter((a) => {
    if (a.status !== "OPEN" || !severities.includes(a.severity)) return false;
    const at = last.get(a.id);
    return at === undefined || (a.severity === "CRITICAL" && now - at >= REPEAT_MS);
  });
  for (const a of due) last.set(a.id, now);
  return due;
}

/** The alerts open when the app starts: already on screen in the alerts strip, so they don't pop
    up, but a critical one still repeats if nobody acknowledges it. */
export const openSince = (alerts: ServerAlert[], now: number) =>
  new Map(alerts.filter((a) => a.status === "OPEN").map((a) => [a.id, now]));

/** Critical first, then newest first. */
export const toastOrder = (a: ServerAlert, b: ServerAlert) =>
  Number(b.severity === "CRITICAL") - Number(a.severity === "CRITICAL") ||
  b.raisedAt.localeCompare(a.raisedAt);
