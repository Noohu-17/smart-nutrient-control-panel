// src/features/dosing/stockShown.ts · whether the dose card shows what each line's stock is (name,
// compound, strength, purpose). Shown unless the person hides it; remembered in this browser.
import { useSyncExternalStore } from "react";

const KEY = "snm.showStock";
const listeners = new Set<() => void>();

function read(): boolean {
  try {
    return localStorage.getItem(KEY) !== "hidden";
  } catch {
    return true; // storage blocked: shown, the default
  }
}

let shown = read();

function setShown(next: boolean) {
  shown = next;
  try {
    if (next) localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, "hidden");
  } catch {
    /* storage blocked: the choice still applies until the page reloads */
  }
  for (const listener of listeners) listener();
}

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

/** [shown, setShown]: one setting for every dose card on the page. */
export function useStockShown(): [boolean, (next: boolean) => void] {
  return [useSyncExternalStore(subscribe, () => shown), setShown];
}
