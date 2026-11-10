/** What a grower calls each device kind (contract §2.2). An unknown kind shows as sent. */
export const KIND_TEXT: Record<string, string> = {
  GATEWAY: "Gateway",
  ZONE_CONTROLLER: "Zone controller",
  CLIMATE_CONTROLLER: "Climate controller",
  POWER_MONITOR: "Power monitor",
};

/** "Just now", "4 min ago", "2 h ago", the date for anything older than a day, or "Never heard from". */
export function lastSeenText(lastSeen: string | null, now = Date.now()) {
  if (!lastSeen) return "Never heard from";
  const minutes = Math.floor((now - new Date(lastSeen).getTime()) / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ago`;
  return new Date(lastSeen).toLocaleDateString();
}
