import { useEffect, useMemo, useState, type ReactNode } from "react";
import { setDemoRole } from "../api/client";
import { isDemoMode } from "../config/demo";
import { ROLES, type Role } from "./roles";
import { SessionContext } from "./session";

// fake sign-in until Track 1's auth exists: the chosen role is remembered in this browser
const ROLE_KEY = "snm.role";

function savedRole(): Role | null {
  if (!isDemoMode()) return null; // a role picked in a demo build doesn't sign anyone in outside it
  try {
    const role = localStorage.getItem(ROLE_KEY);
    return (ROLES as readonly string[]).includes(role ?? "") ? (role as Role) : null;
  } catch {
    return null;
  }
}

function saveRole(role: Role | null) {
  try {
    if (role) localStorage.setItem(ROLE_KEY, role);
    else localStorage.removeItem(ROLE_KEY);
  } catch {
    /* storage blocked: the role still applies in this tab */
  }
}

/** Tests pass `initialRole` (a role, or null for signed out) and never touch browser storage. */
export function SessionProvider({
  children,
  initialRole,
}: {
  children: ReactNode;
  initialRole?: Role | null;
}) {
  const fromTest = initialRole !== undefined;
  const [role, setRole] = useState<Role | null>(() => (fromTest ? initialRole : savedRole()));
  // every write carries the role, so the back end can record who did what (demo sites only)
  useEffect(() => setDemoRole(role), [role]);

  const session = useMemo(() => {
    const change = (next: Role | null) => {
      setRole(next);
      if (!fromTest) saveRole(next);
    };
    return { role, signIn: (r: Role) => change(r), signOut: () => change(null) };
  }, [role, fromTest]);

  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
