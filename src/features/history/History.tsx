import { EmptyState } from "../../components/EmptyState";
import { RoleButton } from "../../components/RoleButton";

/** History skeleton: every dose, refill and stop arrives with GET /api/operations. */
export function History() {
  return (
    <>
      <div className="phead">
        <h1>History</h1>
        <div className="actions">
          <RoleButton perm="export">Export CSV</RoleButton>
        </div>
      </div>
      <EmptyState title="No operations yet">
        Doses, refills and stops are listed here with their result.
      </EmptyState>
    </>
  );
}
