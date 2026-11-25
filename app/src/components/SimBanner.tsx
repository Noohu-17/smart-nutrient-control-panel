import { noSimulator, useFarm, useSimState } from "../api/hooks";

/** "Running 60× faster (1 real minute = 1 farm hour)", "Running at real speed", "Paused". */
function speedText(x: number) {
  if (x === 0) return "Paused";
  if (x === 1) return "Running at real speed";
  const farmHours = x / 60;
  const perMinute = Number.isInteger(farmHours)
    ? `${farmHours} farm ${farmHours === 1 ? "hour" : "hours"}`
    : `about ${Math.round(farmHours)} farm hours`;
  return `Running ${x}× faster (1 real minute = ${perMinute})`;
}

/** On every screen of a playground site: "Simulated farm · Farm clock: 9 Nov, 19:15 · Running 60×
    faster (1 real minute = 1 farm hour)". The farm clock is the simulator's (contract §11.4);
    readings' own times stay real. Nothing on a real farm, or when the back end says the site has
    no simulator (404). */
export function SimBanner() {
  const farm = useFarm();
  const playground = farm.data?.features.playground === true;
  const sim = useSimState(playground);
  if (!playground || noSimulator(sim.error)) return null;

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
      {sim.data && ` · Farm clock: ${when} · ${speedText(sim.data.speed)}`}
    </div>
  );
}
