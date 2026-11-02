import { EmptyState } from "../../components/EmptyState";
import { RoleButton } from "../../components/RoleButton";

/** Climate skeleton: air conditions and equipment arrive with GET /api/climate. */
export function Climate() {
  return (
    <>
      <div className="phead">
        <h1>Climate</h1>
        <div className="actions">
          <RoleButton perm="operate">Change mode</RoleButton>
          <RoleButton perm="operate">Control equipment</RoleButton>
        </div>
      </div>
      <EmptyState title="No climate readings yet">
        Air temperature, humidity and equipment show here once the climate controller reports.
      </EmptyState>
    </>
  );
}
