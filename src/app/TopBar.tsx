import { LiveChip } from "../components/LiveChip";
import { SITE_NAME } from "./site";

export function TopBar() {
  return (
    <header className="topbar">
      <b>{SITE_NAME}</b>
      <LiveChip />
    </header>
  );
}
