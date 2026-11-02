/* Who may do what (contract §13). A hint for the screens only: the server enforces every rule,
   and a disabled button is never the protection. */

export const ROLES = ["Owner", "Agronomist", "Operator", "Viewer"] as const;
export type Role = (typeof ROLES)[number];

export type Permission = "operate" | "ack" | "export" | "recipe" | "clearFault";

const STAFF: Role[] = ["Owner", "Agronomist", "Operator"];

export const CAN: Record<Permission, Role[]> = {
  operate: STAFF, // dose, refill, climate mode and equipment
  ack: STAFF, // acknowledge alerts
  export: STAFF,
  recipe: ["Owner", "Agronomist"],
  clearFault: ["Owner"],
};

/** One-line reason shown under a locked button. */
export const WHY: Record<Permission, string> = {
  operate: "Viewers can’t operate equipment",
  ack: "Viewers can’t acknowledge alerts",
  export: "Viewers can’t export",
  recipe: "Only Owners and Agronomists change recipes",
  clearFault: "Only Owners clear a latched fault",
};

export const can = (role: Role, perm: Permission) => CAN[perm].includes(role);

/** What each role is for, shown on the sign-in screen. */
export const ROLE_TEXT: Record<Role, string> = {
  Owner: "Everything, including faults, limits and users",
  Agronomist: "Recipes, dosing, refills and climate",
  Operator: "Day-to-day dosing, refills and climate",
  Viewer: "Sees everything, can stop but not operate",
};
