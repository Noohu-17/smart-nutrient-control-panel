import { useFarm } from "../api/hooks";
import { LiveChip } from "../components/LiveChip";

export function TopBar() {
  const farm = useFarm();
  return (
    <header className="topbar">
      <b>{farm.data?.name ?? "…"}</b>
      <LiveChip />
    </header>
  );
}
