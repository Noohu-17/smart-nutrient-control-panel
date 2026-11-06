import type { ReactNode } from "react";
import { Icon } from "../../components/Icon";

/** A button on the Simulator screen. Locked, it stays visible with a lock and points at the
    card's one reason line (`reasonId`), so a row of locked buttons doesn't repeat the reason. */
export function SimButton({
  children,
  onClick,
  reasonId,
  label,
  pressed,
  disabled = false,
}: {
  children: ReactNode;
  onClick: () => void;
  /** set when the role can't use it: the id of the line that says why */
  reasonId?: string;
  /** the full name for screen readers when the text is short, e.g. "Fix Leak sensor wet on Zone B" */
  label?: string;
  pressed?: boolean;
  disabled?: boolean;
}) {
  const locked = Boolean(reasonId);
  return (
    <button
      type="button"
      className="btn"
      aria-label={label}
      aria-pressed={pressed}
      aria-describedby={reasonId}
      disabled={locked || disabled}
      onClick={onClick}
    >
      {locked && <Icon name="lock" size={16} />}
      {children}
    </button>
  );
}

/** The one line saying why a card's controls are locked. Nothing when they aren't. */
export function LockReason({ id, reason }: { id: string; reason: string | undefined }) {
  if (!reason) return null;
  return (
    <p id={id} className="lbtn-reason sim-reason">
      {reason}
    </p>
  );
}
