// src/api/types.ts · shapes of the API answers (contract §2.4 layout, §6.1 zone, §6.2 climate, §6.3 power)
// Later these are generated from OpenAPI (npm run gen:api); until then we keep them by hand.

/** A dose line ID from the config pack: A, B and ACID at client_A, N, P and K at client_B. */
export type DoseLine = string;

export type Quality = "OK" | "STALE" | "OUT_OF_RANGE" | "FAULT" | "UNCALIBRATED";

/** Where a zone's readings and controller come from (§2.4, T1-33): real hardware or the simulator. */
export type ZoneSource = "LIVE" | "SIMULATED";

/* ---------- GET /api/farm (contract §2.4, layout.json) ---------- */
export type Bench = { id: string; channels: number; lengthM: number };

/** One kind of growing unit (T1-38). The size keys follow the type: channels and lengthM for an
    NFT_BENCH, widthM for a raft, volumeL for a bucket or bag, heightM for a tower. */
export type GrowingUnit = {
  type: string; // NFT_BENCH, DWC_RAFT, DUTCH_BUCKET, GROW_BAG, VERTICAL_TOWER
  count: number;
  plantSitesEach: number;
  channels?: number;
  lengthM?: number;
  widthM?: number;
  volumeL?: number;
  heightM?: number;
};

export type Tank = { id: string; volumeL?: number; usableL: number };

export type ZoneSize = { lengthM: number; widthM: number };

export type ZonePosition = { xM: number; yM: number; label?: string | null };

export type FarmZone = {
  id: string; // "Z1"
  name: string; // "Zone A" (what the screen shows)
  // who runs the zone (§2.4): real hardware on real time, or the simulator. GET /farm always fills it.
  source: ZoneSource;
  controller: string; // "ZC1"
  // what the zone is (T1-38); a pack from before it leaves them out
  growingSystem?: string; // NFT, DWC, DUTCH_BUCKET, DRIP, VERTICAL, MIXED
  growingUnits?: GrowingUnit[];
  size?: ZoneSize;
  position?: ZonePosition;
  notes?: string;
  reservoir: Tank;
  dosingCabinet: string;
  doseLines: DoseLine[];
  sensors?: Record<string, boolean>; // each sensor, fitted (true) or not
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
    controller: string; // "" on a site with no climate controller
    fans: string[];
    padPumps: string[];
    chillers: Record<string, string>;
    shade: string[];
    haf: string[];
    foggers: string[];
    airSensors: string[];
    source?: ZoneSource; // DM-2: who plays the climate controller; left out, as for a zone (§2.4)
  };
  power: { monitor: string; generator: boolean; ups: boolean };
  devices?: { id: string; kind: string; zone?: string }[];
  features: Features;
};

/* ---------- GET /api/sensors (DM-3: sensors.json, the sensor catalogue per farm type) ---------- */
/** Where a fitted sensor's live value is: a field of the zone answer, or of the climate answer. */
export type SensorReading = {
  from: "zone.readings" | "zone" | "climate.readings";
  key: string;
  label: string;
  unit?: string;
  decimals?: number;
  states?: Record<"true" | "false", string>; // a yes/no value, e.g. leak: Dry / Wet
};

export type SensorInfo = {
  id: string;
  name: string;
  measures: string;
  necessity: "MUST_HAVE" | "GOOD_TO_HAVE";
  typical: string;
  indicates: string;
  layoutSensor?: string | null; // the layout zones[].sensors key that says it is fitted
  fittedBy?: "CLIMATE_CONTROLLER" | null; // fitted for the whole house
  readings: SensorReading[];
};

export type SensorCatalogue = {
  v: number;
  growingSystems: Record<string, string>; // each layout growingSystem's farm type
  farmTypes: Record<string, { name: string; sensors: SensorInfo[] }>;
};

/* ---------- GET /api/zones/{zoneId} (contract §11.2, zone response; readings from §6.1) ---------- */
export type ZoneStatus = {
  zone: string;
  source?: ZoneSource; // from the layout (§2.4); left out by a back end from before T1-33
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
  // from the config pack (T1-38); left out by a back end from before it
  profile?: ZoneProfile;
  crop?: ZoneCrop | null; // null: no recipe applied to the zone
};

/** What the zone is (contract §11.2 `profile`): the layout zone, with its controller's link. */
export type ZoneProfile = {
  name: string;
  growingSystem: string | null;
  growingUnits: GrowingUnit[];
  plantSites: number | null; // every unit's plant sites added up
  size: ZoneSize | null;
  position: ZonePosition | null;
  tank: Tank;
  sensors: Record<string, boolean>;
  controller: string;
  link: string; // MQTT, or SF_GATEWAY for a zone on the prototype rig
  tdsPpmPerMsCm?: number | null; // the controller's TDS scale (io_map.json); null when it reports EC
  doseLines: DoseLine[];
  notes: string | null;
};

/** What the zone grows (contract §11.2 `crop`, recipes.json zones). */
export type ZoneCrop = {
  crop: string; // LETTUCE
  name: string | null; // Lettuce
  variety: string | null;
  stage: string; // SEEDLING, GROWING …
  plantedOn: string | null; // the farm's calendar day, YYYY-MM-DD
  ecTarget?: { min: number; max: number } | null; // the stage's targets (recipes.json crops)
  phTarget?: { min: number; max: number } | null;
  daysToHarvest?: number | null; // not in the config pack yet
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
    // §1: calculated by the cloud from air temp and RH; left out by a back end that doesn't yet
    vpdKpa?: number | null;
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

/* ---------- GET /api/zones/{zoneId}/dose-limits (contract §11.2, T1-37, T1-38) ---------- */
/** One line's limits and what is left of its 24 h limit, for the dose form while typing, and what
    the line doses (nutrients.json, T1-38). */
export type DoseLineLimit = {
  line: string;
  // what the line doses; null where the pack doesn't say, left out by a back end from before T1-38
  name?: string | null;
  compound?: string | null;
  strengthPct?: number | null;
  drum?: { id: string; volumeL: number } | null;
  purpose?: string | null;
  pumpMlPerMin?: number | null;
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

/* ---------- operations (contract §11.2, T1-28) ---------- */
export type OperationKind = "DOSE" | "STOP" | "RESUME" | "ACK";

/** GET /api/operations: one action a person took, newest first. */
export type Operation = {
  kind: OperationKind;
  at: string; // when the person did it: the command's issuedAt, or when the alert was acknowledged
  by: { id: string; name: string | null; role: ServerRole | null } | null; // null before sign-in existed
  zone: string | null; // null for a site-wide stop, a climate resume or an alert of no zone
  target: { zone?: string; scope?: string } | null; // the command's target as sent (§7); null for an ACK
  cmdId: string | null; // DOSE, STOP and RESUME
  result: { state: CommandState | "ACKNOWLEDGED"; reason: string | null };
  lines: CommandLine[] | null; // DOSE only, once the controller reports
  before: EcPh | null; // DOSE only
  after: EcPh | null; // DOSE only, after the mix wait
  alert: { id: string; kind: string; severity: ServerAlert["severity"]; message: string } | null; // ACK only
};

/** `next`: pass it back as `before` for the next, older page; null on the last page. */
export type OperationList = { items: Operation[]; next: string | null };

/* ---------- Web Push (contract §11.2, T1-41) ---------- */
/** The severities a browser can be pushed: never INFO. CRITICAL always, WARNING if asked for. */
export type PushSeverity = "CRITICAL" | "WARNING";

/** GET /api/push/key: the server's VAPID public key, base64url, for PushManager.subscribe. */
export type PushKey = { publicKey: string };

/** POST /api/push/subscriptions: PushSubscription.toJSON() and the severities; the answer echoes them. */
export type PushSubscriptionBody = {
  endpoint: string;
  keys: { p256dh: string; auth: string };
  severities: PushSeverity[];
};
export type PushSubscriptionAnswer = { endpoint: string; severities: PushSeverity[] };

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
