import { EmptyState } from "../../components/EmptyState";
import { RoleButton } from "../../components/RoleButton";

/** Alerts skeleton: open alerts arrive with GET /api/alerts. */
export function Alerts() {
  return (
    <>
      <div className="phead">
        <h1>Alerts</h1>
        <div className="actions">
          <RoleButton perm="ack">Acknowledge all</RoleButton>
        </div>
      </div>
      <EmptyState title="No open alerts">Nothing needs attention right now.</EmptyState>
    </>
  );
}
