import type { FarmZone } from "../api/types";
import type { IconName } from "../components/Icon";
import { zonePath } from "../config/readings";

export type NavItem = { to: string; label: string; icon: IconName };

/** Monitor group: Overview, then one link per zone from GET /api/farm. The house climate is on each
    zone page, not a page of its own. */
export const navMonitor = (zones: FarmZone[]): NavItem[] => [
  { to: "/", label: "Overview", icon: "grid" },
  ...zones.map((z) => ({ to: zonePath(z.id), label: z.name, icon: "layers" as const })),
];

/** System group. The Simulator exists only on playground sites (contract 11.4.1). */
export const navSystem = (playground = false): NavItem[] => [
  { to: "/alerts", label: "Alerts", icon: "bell" },
  { to: "/history", label: "History", icon: "clock" },
  ...(playground ? [{ to: "/simulator", label: "Simulator", icon: "sliders" as const }] : []),
];
