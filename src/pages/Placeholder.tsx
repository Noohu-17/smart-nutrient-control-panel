/** Page frame for screens that are built in later tasks. */
export function Placeholder({ title, note = "Coming in a later task." }: { title: string; note?: string }) {
  return (
    <div className="phead">
      <h1>{title}</h1>
      <p>{note}</p>
    </div>
  );
}
