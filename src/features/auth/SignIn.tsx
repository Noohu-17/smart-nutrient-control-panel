import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ROLE_TEXT, ROLES } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { Icon } from "../../components/Icon";

/** Fake sign-in: pick one of the four roles. Replaced by real sign-in (POST /auth/login) later. */
export function SignIn() {
  const { role, signIn } = useSession();
  const navigate = useNavigate();
  // after signing in, go back to the screen the person was on
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/";
  if (role) return <Navigate to={from} replace />;

  return (
    <main className="signin">
      <div className="signin-card">
        <div className="brand">
          <div className="logo">
            <Icon name="drop" size={24} filled />
          </div>
          <div>
            <b>Smart Nutrient Management</b>
            <span>Control portal · Phase 2</span>
          </div>
        </div>
        <h1>Sign in as</h1>
        <p>Demo sign-in: pick a role to see what it can do.</p>
        <div className="role-list">
          {ROLES.map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => {
                signIn(r);
                navigate(from, { replace: true });
              }}
            >
              <b>{r}</b>
              <span>{ROLE_TEXT[r]}</span>
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}
