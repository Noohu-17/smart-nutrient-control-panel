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

/* ---------- GET /api/climate (contract §11.2, climate answer; readings from §6.2) ---------- */
export type ClimateStatus = {
  online: boolean; // as for a zone: false after 3 minutes' silence or the controller's last will
  lastSeen: string | null;
  ts: string | null; // when the latest reading was taken
  simTs?: string; // playground sites only: the simulated time
  readings: {
    airTempC: number | null; // average of the positions
    airTempByPosC: Record<string, number | null>; // keys from layout.climate.airSensors
    airRh: number | null;
    outsideTempC: number | null;
    outsideRh: number | null;
    elecRoomTempC: number | null;
  };
  // {} until the controller first reports; OK becomes STALE past the fresh limit or when offline
  quality: Partial<Record<"airTempC" | "airRh" | "outsideTempC" | "outsideRh" | "elecRoomTempC", Quality>>;
  // the latest CLIMATE_STATE (§6.9); null until it first arrives
  mode: "AUTOMATIC" | "MANUAL" | null;
  manualUntil: string | null;
  stopped: boolean | null;
  equipment: Record<string, string | number | { state: string; setpointC: number }>;
  latches: string[];
};

/* ---------- GET /api/power (contract §11.2, power answer; values from §6.3) ---------- */
export type PowerStatus = {
  online: boolean;
  lastSeen: string | null;
  ts: string | null;
  simTs?: string;
  // null until the monitor first reports
  mains: "OK" | "LOST" | "PHASE_FAULT" | null;
  phases: { l1V: number | null; l2V: number | null; l3V: number | null };
  source: "MAINS" | "GENERATOR" | "UPS_ONLY" | null;
  // left out when the layout has none; null when it has one but nothing has arrived
  generator?: "STOPPED" | "STARTING" | "RUNNING" | "FAULT" | null;
  ats: "MAINS" | "GENERATOR" | null;
  ups?: { onBattery: boolean; batteryPct: number | null; runtimeMin: number | null } | null;
  // {} while fresh; every value STALE once the message is over the fresh limit or the monitor is offline
  quality: Partial<Record<"l1V" | "l2V" | "l3V" | "upsBatteryPct" | "upsRuntimeMin", Quality>>;
};

/* ---------- POST /api/dose-requests (contract §11.3) ---------- */
export type DoseLineAmount = { line: DoseLine; ml: number };

export type DoseRequestBody = { zone: string; lines: DoseLineAmount[] };

export type CheckResult = "PASS" | "WARN" | "BLOCK";

export type DoseCheck = { id: string; result: CheckResult; message: string };

export type DoseRequest = {
  id: string;
  zone: string;
  lines: DoseLineAmount[];
  projected: { ec: number | null; ph: number | null };
  checks: DoseCheck[];
  estimate: { durationMin: number; totalMl: number };
  canConfirm: boolean;
};

/* ---------- POST /api/dose-requests/{id}/confirm (contract §11.3, states §8.1) ---------- */
export type DoseConfirmed = { cmdId: string; state: "DISPATCHED" };

/* ---------- GET /api/zones/{zoneId}/dose-limits (contract §11.2, T1-37) ---------- */
/** One line's limits and what is left of its 24 h limit, for the dose form while typing. */
export type DoseLineLimit = {
  line: string;
  maxPerDoseMl: number;
  max24hMl: number;
  dosed24hMl: number; // delivered in the last 24 h, as the LIMIT_24H check counts it
  left24hMl: number; // never below 0
};

export type DoseLimits = { zone: string; lines: DoseLineLimit[] };

/* ---------- Simulator control, playground sites only (contract §11.4) ---------- */
/** Clock speed: 0 is paused, 288 is one simulated day in five minutes. */
export type SimSpeed = 0 | 1 | 60 | 288;

/** A failure drill that is running: Trigger turned it on, Fix turns it off (§11.4.3). */
export type SimFault = { kind: string; target: string };

/** A value set and held until released (§11.4.2). `target` is a zone ID or "POLYHOUSE". */
export type SimHold = { target: string; metric: string; value: number; gradual: boolean };

/** GET /api/sim/state. Field names follow the `sim_state` WebSocket message (§12). */
export type SimState = {
  scenario: string;
  simTime: string; // the simulated date and time; every message's ts stays real time
  speed: SimSpeed;
  weather: string;
  faults: SimFault[];
  holds: SimHold[];
};

export type SimRange = { min: number; max: number };

/** One entry of the failure-drill catalogue in scenarios.json (§11.4.3). */
export type FailureDrill = {
  kind: string;
  group: string;
  prdCode: string;
  targetType: "ZONE" | "ZONE_SENSOR" | "DOSE_LINE" | "POLYHOUSE" | "DEVICE";
  title: string;
  explanation: string;
  fix: string;
  params: Record<string, unknown>;
};

/** GET /api/sim/scenario-options: the client's scenarios.json, the parts the app reads so far. */
export type ScenarioOptions = {
  failureDrills: FailureDrill[];
  holdable: {
    zone: Record<string, SimRange & { linesFrom?: string }>;
    polyhouse: Record<string, SimRange>;
  };
};

export type SimFaultBody = SimFault & { on: boolean };

/* ---------- GET /api/commands/{cmdId} (contract §6.6, §8) ---------- */
export type CommandState =
  | "DISPATCHED"
  | "ACCEPTED"
  | "EXECUTING"
  | "COMPLETED"
  | "PARTIAL"
  | "FAILED"
  | "STOPPED"
  | "REJECTED"
  | "EXPIRED"
  | "UNKNOWN";

export type LineState = "PENDING" | "RUNNING" | "DONE" | "SHORT" | "SKIPPED";

export type CommandLine = { line: DoseLine; requestedMl: number; deliveredMl: number; state: LineState };

export type EcPh = { ec: number | null; ph: number | null };

/** GET /api/commands/{cmdId}: the command as its controller last reported it (CMD_STATE §6.6).
    After the last line a COMPLETED or PARTIAL comes with `after: null`; the same state comes
    again with `after` once the mix wait is over. */
export type Command = {
  cmdId: string;
  device: string;
  state: CommandState;
  reason: string | null; // §8.4 code
  lines: CommandLine[] | null; // DOSE only
  before: EcPh | null;
  after: EcPh | null;
  startedAt: string | null;
  endedAt: string | null;
};

/* ---------- alerts (contract §11.2, T1-24) ---------- */
export type ServerAlert = {
  id: string;
  kind: string; // EC_HIGH, RESERVOIR_LOW, ESTOP_PRESSED …
  severity: "CRITICAL" | "WARNING" | "INFO";
  status: "OPEN" | "ACKNOWLEDGED" | "CLEARED"; // CLEARED: the condition is over
  zone: string | null; // null for the climate controller's
  message: string; // what happened
  systemAction: string; // what the farm did
  nextStep: string; // what a person should do
  raisedAt: string;
  simTs: string | null; // the farm clock, playground sites only
  clearedAt: string | null;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
};

/* ---------- sign-in (contract §11.1, §11.2, T1-35: backend/app/auth/README.md) ---------- */
export type ServerRole = "OWNER" | "AGRONOMIST" | "OPERATOR" | "VIEWER";

/** GET /api/me, and the answer to login, demo login and a right two-factor code. */
export type Me = {
  user: { id: string; name: string; email: string; role: ServerRole };
  csrfToken: string; // sent as X-CSRF-Token on every request that isn't a GET
  expiresAt: string; // the session ends then; every call answers 401 after it
  step: "DONE" | "TOTP" | "TOTP_SETUP"; // what sign-in still needs: nothing, the code, or setting up two-factor
};

/** POST /api/auth/totp {} at step TOTP_SETUP: the new secret for the authenticator app. */
export type TotpSetup = { secret: string; otpauthUri: string };

/* ---------- errors (contract §11.1) ---------- */
export type ApiErrorBody = { error: { code: string; message: string; details?: unknown } };
