/** T2-17 · demo mode: the role picker on the sign-in page and the "Demo · simulated farm" banner.
    A build has it only with VITE_DEMO_MODE=true (staging, T1-19), so a build without the flag never
    looks like a demo. Development has it unless VITE_DEMO_MODE=false, like the fake API (client.ts).
    Read on every call, so tests can switch it with vi.stubEnv. */
export function isDemoMode() {
  const flag = import.meta.env.VITE_DEMO_MODE;
  return flag === "true" || (import.meta.env.DEV && flag !== "false");
}
