// src/api/types.ts · shapes of the API answers (contract §2.4 layout, §6.1 zone, §6.2 climate, §6.3 power)
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

/** Per-site switches (contract §2.5, features.json). */
export type Features = {
  playground: boolean;
  demoFarm: boolean;
  climateAutomatic: boolean;
  autoRefill: boolean;
  secondApproval: boolean;
  foggers: boolean;
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
  features: Features;
};

/* ---------- GET /api/zones/{zoneId} (contract §11.2, zone response; readings from §6.1) ---------- */
export type ZoneStatus = {
  zone: string;
  online: boolean; // false once the controller has been silent 3 minutes; the last values still come
  lastSeen: string | null; // newest ts of any message from the controller; null if never heard from
  ts: string | null; // when the latest reading was taken
  simTs?: string; // playground sites only
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
  // {} until the zone first reports; OK becomes STALE past the fresh limit or when offline
  quality: Partial<Record<"ec" | "ph" | "solTempC" | "reservoirPct" | "circFlowLpm", Quality>>;
  equipment: Record<string, "ON" | "OFF" | "OPEN" | "CLOSED">;
  // null = not known yet (never reported, or ZONE_STATE not stored yet)
  mode: "REMOTE" | "LOCAL" | "MAINT" | null;
  estop: boolean | null;
  leak: boolean | null;
  stopped: boolean | null;
  activeCmdId: string | null;
  operation: { cmdId: string; kind: "DOSE" | "REFILL"; state: string; line?: string | null } | null;
  latches: string[];
  configVersion: number | null;
};

/* ---------- GET /api/climate (contract §6.2, CLIMATE_TELEMETRY) ---------- */
export type ClimateTelemetry = {
  v: number;
  type: "CLIMATE_TELEMETRY";
  msgId: string;
  ts: string;
  simTs?: string; // playground sites only: the simulated time
  site: string;
  device: string;
  bootId: number;
  seq: number;
  readings: {
    airTempC: number | null; // average of the positions
    airTempByPosC: Record<string, number | null>; // keys from layout.climate.airSensors
    airRh: number | null;
    outsideTempC: number | null;
    outsideRh: number | null;
    elecRoomTempC: number | null;
  };
  quality: Partial<Record<"airTempC" | "airRh" | "outsideTempC" | "outsideRh" | "elecRoomTempC", Quality>>;
  mode: "AUTOMATIC" | "MANUAL";
  manualUntil: string | null;
  equipment: Record<string, string | number | { state: string; setpointC: number }>;
  runTimeTodayMin: Record<string, number>;
  setpoints: { airTempMaxC: number; airRhMax: number; solTempMaxC: number };
};

/* ---------- GET /api/power (contract §6.3, POWER_STATE) ---------- */
export type PowerState = {
  v: number;
  type: "POWER_STATE";
  msgId: string;
  ts: string;
  simTs?: string;
  site: string;
  device: string;
  bootId: number;
  seq: number;
  mains: "OK" | "LOST" | "PHASE_FAULT";
  phases: { l1V: number | null; l2V: number | null; l3V: number | null };
  source: "MAINS" | "GENERATOR" | "UPS_ONLY";
  generator?: "STOPPED" | "STARTING" | "RUNNING" | "FAULT"; // left out when layout.power.generator is false
  ats: "MAINS" | "GENERATOR";
  ups?: { onBattery: boolean; batteryPct: number; runtimeMin: number }; // left out when layout.power.ups is false
};

/* ---------- errors (contract §11.1) ---------- */
export type ApiErrorBody = { error: { code: string; message: string; details?: unknown } };
