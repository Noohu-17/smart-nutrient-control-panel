/** Connection chip. Nothing is live yet, so it shows the neutral offline look
    until the live connection (WebSocket) is wired up. */
export function LiveChip() {
  return (
    <span className="live-chip">
      <i />
      Not connected
    </span>
  );
}
