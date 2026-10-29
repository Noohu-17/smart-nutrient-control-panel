import type { IconName } from "../components/Icon";

export type NavItem = { to: string; label: string; icon: IconName };

/** Zones are fixed for now; they will come from /api/farm later. IDs are the contract's (Z1, Z2); the name is only for display. */
export const ZONES = [
  { id: "Z1", name: "Zone A" },
  { id: "Z2", name: "Zone B" },
];

export const zonePath = (id: string) => `/zone/${id}`;

export const NAV_MONITOR: NavItem[] = [
  { to: "/", label: "Overview", icon: "grid" },
  ...ZONES.map((z) => ({ to: zonePath(z.id), label: z.name, icon: "layers" as const })),
  { to: "/climate", label: "Climate", icon: "therm" },
];
export const NAV_SYSTEM: NavItem[] = [
  { to: "/alerts", label: "Alerts", icon: "bell" },
  { to: "/history", label: "History", icon: "clock" },
];
