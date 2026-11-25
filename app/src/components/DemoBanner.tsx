import { isDemoMode } from "../config/demo";

/** On every screen in demo mode, so nobody takes the simulated farm for a real one. Nothing otherwise. */
export function DemoBanner() {
  if (!isDemoMode()) return null;
  return (
    <div className="demo-banner" role="note">
      <b>Demo</b> · simulated farm
    </div>
  );
}
