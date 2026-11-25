import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { TotpSetup } from "../../api/types";
import { ROLE_TEXT, ROLES } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { DemoBanner } from "../../components/DemoBanner";
import { Icon } from "../../components/Icon";
import { isDemoMode } from "../../config/demo";

const message = (e: unknown) => (e instanceof ApiError ? e.message : "Sign-in didn’t work. Try again");

/** T2-27 · sign-in against the server (T1-35). Demo mode: pick a role (POST /api/auth/demo).
    Otherwise email and password (POST /api/auth/login), then the authenticator code for an Owner. */
export function SignIn() {
  const { role, step, checking } = useSession();
  // after signing in, go back to the screen the person was on
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/";
  // kept here, so "Too many wrong codes" still shows once the page is back at the password
  const [error, setError] = useState<string | null>(null);
  if (role) return <Navigate to={from} replace />;

  return (
    <>
      <DemoBanner />
      <main className="signin">
        <div className="signin-card">
          <div className="brand">
            <div className="logo">
              <Icon name="drop" size={24} filled />
            </div>
            <div>
              <b>Smart Nutrient Management</b>
            </div>
          </div>
          {checking ? (
            <p role="status">Checking sign-in…</p>
          ) : step === "TOTP" ? (
            <CodeForm onError={setError} />
          ) : step === "TOTP_SETUP" ? (
            <SetUpTwoFactor onError={setError} />
          ) : isDemoMode() ? (
            <RolePicker onError={setError} />
          ) : (
            <PasswordForm onError={setError} />
          )}
          {error && (
            <p className="signin-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </main>
    </>
  );
}

type StepProps = { onError: (error: string | null) => void };

/** Runs a sign-in call: busy meanwhile, its error shown, the last error cleared first. */
function useAttempt(onError: StepProps["onError"]) {
  const [busy, setBusy] = useState(false);
  const attempt = async (call: () => Promise<unknown>) => {
    setBusy(true);
    onError(null);
    try {
      await call();
    } catch (e) {
      onError(message(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, attempt };
}

function RolePicker({ onError }: StepProps) {
  const { demoSignIn } = useSession();
  const { busy, attempt } = useAttempt(onError);
  return (
    <>
      <h1>Sign in as</h1>
      <p>Demo sign-in: pick a role to see what it can do.</p>
      <div className="role-list">
        {ROLES.map((r) => (
          <button key={r} type="button" disabled={busy} onClick={() => void attempt(() => demoSignIn(r))}>
            <b>{r}</b>
            <span>{ROLE_TEXT[r]}</span>
          </button>
        ))}
      </div>
    </>
  );
}

function SignInForm({ onSubmit, children }: { onSubmit: () => void; children: ReactNode }) {
  return (
    <form
      className="signin-form"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        onSubmit();
      }}
    >
      {children}
    </form>
  );
}

function PasswordForm({ onError }: StepProps) {
  const { signIn } = useSession();
  const { busy, attempt } = useAttempt(onError);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  return (
    <>
      <h1>Sign in</h1>
      <SignInForm onSubmit={() => void attempt(() => signIn(email, password))}>
        <label htmlFor="signin-email">Email</label>
        <input
          id="signin-email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <label htmlFor="signin-password">Password</label>
        <input
          id="signin-password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </SignInForm>
    </>
  );
}

/** The 6-digit code from the authenticator app (step TOTP, and the end of setting it up). */
function CodeForm({ onError, heading = true }: StepProps & { heading?: boolean }) {
  const { sendCode, signOut } = useSession();
  const { busy, attempt } = useAttempt(onError);
  const [code, setCode] = useState("");
  return (
    <>
      {heading && (
        <>
          <h1>Two-factor code</h1>
          <p>Enter the 6-digit code your authenticator app shows for Smart Nutrient.</p>
        </>
      )}
      <SignInForm onSubmit={() => void attempt(() => sendCode(code.trim()))}>
        <label htmlFor="signin-code">Code</label>
        <input
          id="signin-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="[0-9]{6}"
          maxLength={6}
          required
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Checking…" : "Continue"}
        </button>
        <button type="button" className="signin-link" onClick={() => void signOut()}>
          Use another account
        </button>
      </SignInForm>
    </>
  );
}

/** An Owner's first sign-in: add the site to an authenticator app, then send its code. */
function SetUpTwoFactor({ onError }: StepProps) {
  const { setUpTwoFactor } = useSession();
  const { busy, attempt } = useAttempt(onError);
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  return (
    <>
      <h1>Set up two-factor</h1>
      <p>An Owner signs in with a code from an authenticator app too. Set it up once, now.</p>
      {setup ? (
        <>
          <p>
            Add this key to the authenticator app, or <a href={setup.otpauthUri}>open it in the app</a> on
            this phone:
          </p>
          <code className="signin-secret" aria-label="Authenticator key">
            {setup.secret}
          </code>
          <CodeForm onError={onError} heading={false} />
        </>
      ) : (
        <button
          type="button"
          className="btn primary"
          disabled={busy}
          onClick={() => void attempt(async () => setSetup(await setUpTwoFactor()))}
        >
          Show the key
        </button>
      )}
    </>
  );
}
