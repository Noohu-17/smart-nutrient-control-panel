import { useNavigate } from "react-router-dom";
import { RoleButton } from "../../components/RoleButton";
import { zonePath } from "../../config/readings";
import { DOSE_CARD, goToDoseCard } from "./doseCard";

/** The zone's actions, locked per role (contract §13). The zone page adds the less common ones.
    On the Overview, Dose opens the zone page at its dose panel. */
export function ZoneActions({ zoneId, full = false }: { zoneId: string; full?: boolean }) {
  const navigate = useNavigate();
  const dose = full ? goToDoseCard : () => navigate(`${zonePath(zoneId)}#${DOSE_CARD}`);
  return (
    <div className="actions">
      <RoleButton perm="operate" onClick={dose}>
        Dose
      </RoleButton>
      <RoleButton perm="operate">Refill</RoleButton>
      {full && <RoleButton perm="recipe">Change recipe</RoleButton>}
      {full && <RoleButton perm="clearFault">Clear fault</RoleButton>}
    </div>
  );
}
