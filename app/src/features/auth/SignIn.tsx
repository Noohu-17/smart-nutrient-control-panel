import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ROLE_TEXT, ROLES } from "../../auth/roles";
import { useSession } from "../../auth/session";
import { DemoBanner } from "../../components/DemoBanner";
import { Icon } from "../../components/Icon";
import { isDemoMode } from "../../config/demo";

/** Demo mode: pick one of the four roles. Otherwise no picker: real sign-in (POST /auth/login) comes later. */
export function SignIn() {
  const { role, signIn } = useSession();
  const navigate = useNavigate();
  // after signing in, go back to the screen the person was on
  const from = (useLocation().state as { from?: string } | null)?.from ?? "/";
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
          {isDemoMode() ? (
            <>
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
            </>
          ) : (
            <>
              <h1>Sign in</h1>
              <p>Sign-in with your own account is coming soon. Until then this portal can’t be opened.</p>
            </>
          )}
        </div>
      </main>
    </>
  );
}
