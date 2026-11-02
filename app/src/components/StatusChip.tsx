export type ChipTone = "ok" | "warn" | "crit" | "off" | "neutral";

/** Small status pill. The text is required: colour never carries the meaning alone. */
export function StatusChip({ tone = "neutral", children }: { tone?: ChipTone; children: string }) {
  return <span className={`chip chip-${tone}`}>{children}</span>;
}
