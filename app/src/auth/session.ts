import { createContext, useContext } from "react";
import type { Role } from "./roles";

export type Session = {
  /** null until someone signs in */
  role: Role | null;
  signIn: (role: Role) => void;
  signOut: () => void;
};

export const SessionContext = createContext<Session | null>(null);

export function useSession() {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside <SessionProvider>");
  return session;
}
