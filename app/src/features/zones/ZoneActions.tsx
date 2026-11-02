import { LockedButton } from "../../components/LockedButton";

const NOT_BUILT = "Dosing and refill arrive in a later task";

/** The zone's start actions. Shown on the Overview card and the zone page, never hidden. */
export function ZoneActions() {
  return (
    <div className="actions">
      <LockedButton reason={NOT_BUILT}>Dose</LockedButton>
      <LockedButton reason={NOT_BUILT}>Refill</LockedButton>
    </div>
  );
}
