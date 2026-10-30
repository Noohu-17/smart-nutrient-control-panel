/** One reading: label, value with unit, and a note (the quality reason) under it. */
export function ReadingTile({
  label,
  value,
  unit,
  note,
  warn = false,
}: {
  label: string;
  value: string;
  unit: string;
  note?: string;
  warn?: boolean;
}) {
  return (
    <div className={`tile${warn ? " warn" : ""}`}>
      <span className="tile-label">{label}</span>
      <span className="tile-value">
        <b>{value}</b>
        {unit && <small>{unit}</small>}
      </span>
      {note && <span className="tile-note">{note}</span>}
    </div>
  );
}
