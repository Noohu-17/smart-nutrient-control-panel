// src/api/types.ts · shapes of the API answers (contract §2.4 layout and §6.1 zone telemetry)
// Later these are generated from OpenAPI (npm run gen:api); until then we keep them by hand.

export type DoseLine = "A" | "B" | "ACID";

export type Quality = "OK" | "STALE" | "OUT_OF_RANGE" | "FAULT" | "UNCALIBRATED";

/* ---------- GET /api/farm (contract §2.4, layout.json) ---------- */
export type Bench = { id: string; channels: number; lengthM: number };

export type FarmZone = {
  id: string; // "Z1"
  name: string; // "Zone A" (what the screen shows)
  controller: string; // "ZC1"
  reservoir: { id: string; usableL: number };
  dosingCabinet: string;
  doseLines: DoseLine[];
  sensors?: { dissolvedOxygen: boolean };
  dayTanks: Record<DoseLine, { usableL: number }>;
  benches: Bench[];
};

export type Farm = {
  v: number;
  site: string;
  name: string;
  timezone: string;
  zones: FarmZone[];
  nursery: { tables: number };
  climate: {
    controller: string;
    fans: string[];
    padPumps: string[];
    chillers: Record<string, string>;
    shade: string[];
    haf: string[];
    foggers: string[];
    airSensors: string[];
  };
  power: { monitor: string; generator: boolean; ups: boolean };
  devices?: { id: string; kind: string; zone?: string }[];
};

/* ---------- GET /api/zones/{zoneId} (contract §6.1, ZONE_TELEMETRY) ---------- */
export type ZoneTelemetry = {
  v: number;
  type: "ZONE_TELEMETRY";
  msgId: string;
  ts: string; // ISO time the controller sent it
  site: string;
  device: string;
  bootId: number;
  seq: number;
  zone: string;
  readings: {
    ec: number | null; // mS/cm
    ph: number | null;
    solTempC: number | null;
    reservoirPct: number | null;
    reservoirL: number | null;
    circFlowLpm: number | null;
    doMgL: number | null; // null when the zone has no DO sensor
    dayTankPct: Record<DoseLine, number | null>;
    dayTankL: Record<DoseLine, number | null>;
  };
  quality: Partial<Record<"ec" | "ph" | "solTempC" | "reservoirPct" | "circFlowLpm", Quality>>;
  equipment: Record<string, string>;
  mode: "REMOTE" | "LOCAL" | "MAINT";
  estop: boolean;
  leak: boolean;
  activeCmdId: string | null;
};

/* ---------- errors (contract §11.1) ---------- */
export type ApiErrorBody = { error: { code: string; message: string; details?: unknown } };
