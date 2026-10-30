import type { FarmZone } from "../api/types";
import type { IconName } from "../components/Icon";
import { zonePath } from "../config/readings";

export type NavItem = { to: string; label: string; icon: IconName };

/** Monitor group: Overview, one link per zone from GET /api/farm, then Climate. */
export const navMonitor = (zones: FarmZone[]): NavItem[] => [
  { to: "/", label: "Overview", icon: "grid" },
  ...zones.map((z) => ({ to: zonePath(z.id), label: z.name, icon: "layers" as const })),
  { to: "/climate", label: "Climate", icon: "therm" },
];

/** System group. The Simulator exists only on playground sites (contract 11.4.1). */
export const navSystem = (playground = false): NavItem[] => [
  { to: "/alerts", label: "Alerts", icon: "bell" },
  { to: "/history", label: "History", icon: "clock" },
  ...(playground ? [{ to: "/simulator", label: "Simulator", icon: "sliders" as const }] : []),
];
