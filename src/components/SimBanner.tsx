import { useFarm, useSimState } from "../api/hooks";
import { speedLabel } from "../config/sim";

/** On every screen of a playground site: "Simulated farm · ×60" and the simulated date and time
    (contract §11.4). Readings' own times stay real. Nothing on a real farm. */
export function SimBanner() {
  const farm = useFarm();
  const playground = farm.data?.features.playground === true;
  const sim = useSimState(playground);
  if (!playground) return null;

  const when = sim.data
    ? new Intl.DateTimeFormat("en-GB", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: farm.data?.timezone,
      }).format(new Date(sim.data.simTime))
    : null;
  return (
    <div className="demo-banner sim-banner" role="note">
      <b>Simulated farm</b>
      {sim.data && ` · ${speedLabel(sim.data.speed)} · ${when}`}
    </div>
  );
}
