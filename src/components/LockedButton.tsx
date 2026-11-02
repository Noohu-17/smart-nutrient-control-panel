import { useId, type ReactNode } from "react";
import { Icon } from "./Icon";

/** A button that is never hidden: when `reason` is set it is disabled, shows a lock and says why. */
export function LockedButton({
  children,
  reason,
  onClick,
}: {
  children: ReactNode;
  /** one line saying why the action can't be used; leave empty when it can */
  reason?: string;
  onClick?: () => void;
}) {
  const reasonId = useId();
  const locked = Boolean(reason);
  return (
    <span className="lbtn">
      <button
        type="button"
        className="btn"
        disabled={locked}
        aria-describedby={locked ? reasonId : undefined}
        onClick={locked ? undefined : onClick}
      >
        {locked && <Icon name="lock" size={16} />}
        {children}
      </button>
      {locked && (
        <span id={reasonId} className="lbtn-reason">
          {reason}
        </span>
      )}
    </span>
  );
}
