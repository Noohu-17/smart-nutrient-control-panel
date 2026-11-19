import { createContext, useContext } from "react";
import type { Me, TotpSetup } from "../api/types";
import type { Role } from "./roles";

export type SignedInUser = { id: string; name: string; email: string; role: Role };

export type Session = {
  /** null until sign-in is done, two-factor included. Always the server's (GET /api/me, T1-35). */
  role: Role | null;
  user: SignedInUser | null;
  /** What sign-in still needs after the password: the authenticator code, or setting it up. */
  step: Exclude<Me["step"], "DONE"> | null;
  /** True while GET /api/me finds out whether this browser is already signed in. */
  checking: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  /** Demo mode's role picker (POST /api/auth/demo). */
  demoSignIn: (role: Role) => Promise<void>;
  sendCode: (code: string) => Promise<void>;
  /** An Owner's first sign-in: the secret for the authenticator app. */
  setUpTwoFactor: () => Promise<TotpSetup>;
  signOut: () => Promise<void>;
};

export const SessionContext = createContext<Session | null>(null);

export function useSession() {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside <SessionProvider>");
  return session;
}
