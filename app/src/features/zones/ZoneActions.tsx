import { RoleButton } from "../../components/RoleButton";

/** The zone's actions, locked per role (contract §13). The zone page adds the less common ones. */
export function ZoneActions({ full = false }: { full?: boolean }) {
  return (
    <div className="actions">
      <RoleButton perm="operate">Dose</RoleButton>
      <RoleButton perm="operate">Refill</RoleButton>
      {full && <RoleButton perm="recipe">Change recipe</RoleButton>}
      {full && <RoleButton perm="clearFault">Clear fault</RoleButton>}
    </div>
  );
}
