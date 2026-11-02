import { useState, type ReactNode } from "react";
import { can, WHY, type Permission } from "../auth/roles";
import { useSession } from "../auth/session";
import { LockedButton } from "./LockedButton";

/** An action the role may or may not use. Locked with the §13 reason when it can't. Without an
    `onClick` the action is built in a later task, so for now a click only says so. */
export function RoleButton({
  perm,
  children,
  onClick,
}: {
  perm: Permission;
  children: ReactNode;
  onClick?: () => void;
}) {
  const { role } = useSession();
  const [clicked, setClicked] = useState(false);
  const allowed = role !== null && can(role, perm);
  return (
    <span className="lbtn">
      <LockedButton reason={allowed ? undefined : WHY[perm]} onClick={onClick ?? (() => setClicked(true))}>
        {children}
      </LockedButton>
      {clicked && (
        <span className="lbtn-reason" role="status">
          Coming in a later task
        </span>
      )}
    </span>
  );
}
