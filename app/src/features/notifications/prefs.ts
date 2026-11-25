import { useCallback, useSyncExternalStore } from "react";
import type { Severity } from "./notify";

/* Each person's notification choices, kept in this browser under their user ID: which
   severities notify (critical always; warning and info if they choose them), and browser
   notifications (off until they switch them on, which is the only time the browser asks for
   permission). */

export type NotifyPrefs = { severities: Severity[]; browser: boolean };

export const DEFAULT_PREFS: NotifyPrefs = { severities: ["CRITICAL"], browser: false };

const SEVERITIES: Severity[] = ["CRITICAL", "WARNING", "INFO"];
const key = (userId: string) => `snm.notify.${userId}`;
const cache = new Map<string, NotifyPrefs>();
const listeners = new Set<() => void>();

/** Critical always notifies (an E-STOP, a leak, a device offline), whatever was saved before. */
const withCritical = (severities: Severity[]): Severity[] => [
  "CRITICAL",
  ...severities.filter((s) => s !== "CRITICAL" && SEVERITIES.includes(s)),
];

function load(userId: string): NotifyPrefs {
  try {
    const saved = JSON.parse(
      window.localStorage.getItem(key(userId)) ?? "null",
    ) as Partial<NotifyPrefs> | null;
    if (!saved) return DEFAULT_PREFS;
    return { severities: withCritical(saved.severities ?? []), browser: saved.browser === true };
  } catch {
    return DEFAULT_PREFS; // storage blocked or a bad value: the defaults
  }
}

export function readPrefs(userId: string | null): NotifyPrefs {
  if (!userId) return DEFAULT_PREFS;
  if (!cache.has(userId)) cache.set(userId, load(userId));
  return cache.get(userId)!;
}

export function savePrefs(userId: string, choice: NotifyPrefs) {
  const prefs = { ...choice, severities: withCritical(choice.severities) };
  cache.set(userId, prefs);
  try {
    window.localStorage.setItem(key(userId), JSON.stringify(prefs));
  } catch {
    // storage blocked: the choice lasts until the page is reloaded
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The signed-in person's choices, and a way to change them. Everyone using them updates. */
export function useNotifyPrefs(userId: string | null) {
  const prefs = useSyncExternalStore(subscribe, () => readPrefs(userId));
  const update = useCallback(
    (change: Partial<NotifyPrefs>) => {
      if (userId) savePrefs(userId, { ...readPrefs(userId), ...change });
    },
    [userId],
  );
  return [prefs, update] as const;
}

/** Tests start each case with nothing saved. */
export function forgetPrefs() {
  cache.clear();
}
