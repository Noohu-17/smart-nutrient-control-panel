import { useFarm } from "../api/hooks";
import { useSession } from "../auth/session";
import { LiveChip } from "../components/LiveChip";

/** Site name, live state, and who is signed in: their name and the role the server gave them (T2-27). */
export function TopBar() {
  const farm = useFarm();
  const { user, role, signOut } = useSession();
  return (
    <header className="topbar">
      <b>{farm.data?.name ?? "…"}</b>
      <LiveChip />
      <span className="signed-in">
        Signed in as <b>{user?.name}</b> · {role}
      </span>
      {/* the layout sends a signed-out person to /sign-in */}
      <button type="button" className="topbar-btn" onClick={() => void signOut()}>
        Sign out
      </button>
    </header>
  );
}
