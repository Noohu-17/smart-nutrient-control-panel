import { useFarm } from "../api/hooks";
import { useSession } from "../auth/session";
import { LiveChip } from "../components/LiveChip";

/** Site name, live state, and who is signed in. The role stays until Sign out. */
export function TopBar() {
  const farm = useFarm();
  const { role, signOut } = useSession();
  return (
    <header className="topbar">
      <b>{farm.data?.name ?? "…"}</b>
      <LiveChip />
      <span className="signed-in">
        Signed in as <b>{role}</b>
      </span>
      {/* the layout sends a signed-out person to /sign-in */}
      <button type="button" className="topbar-btn" onClick={signOut}>
        Sign out
      </button>
    </header>
  );
}
