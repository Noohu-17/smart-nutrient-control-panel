import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getJson, postJson, setCsrfToken, setOnSignedOut } from "../api/client";
import type { Me, TotpSetup } from "../api/types";
import { unsubscribe as stopPushes } from "../features/notifications/push";
import { fromServerRole, toServerRole } from "./roles";
import { SessionContext, type Session } from "./session";

/** T2-27 · the server's session (T1-35). The browser keeps the cookie; this keeps who it says is
    signed in and the CSRF token. Tests pass `initialMe` (or null for signed out) and skip GET /me. */
export function SessionProvider({ children, initialMe }: { children: ReactNode; initialMe?: Me | null }) {
  const queryClient = useQueryClient();
  const fromTest = initialMe !== undefined;
  const [me, setMe] = useState<Me | null>(fromTest ? initialMe : null);
  const [checking, setChecking] = useState(!fromTest);

  // every write sends the token (client.ts)
  useEffect(() => setCsrfToken(me?.csrfToken ?? null), [me]);

  // any 401 means the session is over: expired, signed out elsewhere, or a new role (T1-35)
  useEffect(() => {
    setOnSignedOut(() => setMe(null));
    return () => setOnSignedOut(() => {});
  }, []);

  // a reload: is this browser still signed in?
  useEffect(() => {
    if (fromTest) return;
    let live = true;
    getJson<Me>("/me")
      .then(
        (answer) => live && setMe(answer),
        () => live && setMe(null),
      )
      .finally(() => live && setChecking(false));
    return () => {
      live = false;
    };
  }, [fromTest]);

  const session = useMemo<Session>(() => {
    const done = me?.step === "DONE";
    return {
      role: me && done ? fromServerRole(me.user.role) : null,
      user: me && done ? { ...me.user, role: fromServerRole(me.user.role) } : null,
      step: me && me.step !== "DONE" ? me.step : null,
      checking,
      signIn: async (email, password) => setMe(await postJson<Me>("/auth/login", { email, password })),
      demoSignIn: async (role) => setMe(await postJson<Me>("/auth/demo", { role: toServerRole(role) })),
      sendCode: async (code) => setMe(await postJson<Me>("/auth/totp", { code })),
      setUpTwoFactor: () => postJson<TotpSetup>("/auth/totp", {}),
      signOut: async () => {
        // the server can't tell which subscription is this browser's, so this browser says so
        // first: the next person on it gets none of this one's alerts (§11.2, T1-41)
        await stopPushes();
        // signed out here even if the server can't be reached; the cookie ends with its session
        await postJson("/auth/logout").catch(() => {});
        setMe(null);
        queryClient.clear(); // nothing of this user's is shown to the next one
      },
    };
  }, [me, checking, queryClient]);

  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
