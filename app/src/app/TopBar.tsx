import { useFarm } from "../api/hooks";
import { useSession } from "../auth/session";
import { Icon } from "../components/Icon";
import { LiveChip } from "../components/LiveChip";

/** Site name, live state, and who is signed in: their name and the role the server gave them (T2-27).
    On a playground site, a switch that shows the Simulator beside the page. */
export function TopBar({ sideBySide }: { sideBySide?: { on: boolean; toggle: () => void } }) {
  const farm = useFarm();
  const { user, role, signOut } = useSession();
  return (
    <header className="topbar">
      <b>{farm.data?.name ?? "…"}</b>
      <LiveChip />
      {sideBySide && (
        <button
          type="button"
          className="topbar-btn sbs-toggle"
          aria-pressed={sideBySide.on}
          onClick={sideBySide.toggle}
        >
          <Icon name="columns" size={16} /> {sideBySide.on ? "Close side by side" : "Side by side"}
        </button>
      )}
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
