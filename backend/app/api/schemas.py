"""What the read API answers (contract §2.4, §11.2), and so what /api/openapi.json describes.

The models come from the contract models, so wire names stay lowerCamelCase. Maps keyed by zones,
dose lines or equipment IDs are plain dicts, because those keys come from the client's config
(OPEN-12), and fields the models don't name in the config pack are passed on unchanged.
"""

from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import UUID4, ConfigDict, Field, PlainSerializer, StrictBool, WithJsonSchema

from ..contract import ContractModel
from ..contract.envelope import Count, Number
from ..contract.farm_messages import (
    AtsPosition,
    ChillerState,
    ClimateMode,
    ClimateOutput,
    ClimateReadings,
    CommandLine,
    EcPh,
    GeneratorState,
    Mains,
    Phases,
    PowerSource,
    Quality,
    UpsState,
    ZoneMode,
    ZoneOperation,
    ZoneOutput,
    ZoneReadings,
)


def contract_time(moment: datetime) -> str:
    """Contract time (§1): ISO-8601 UTC with milliseconds and Z, e.g. 2026-11-09T04:54:00.000Z."""
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


ApiTime = Annotated[
    datetime,
    PlainSerializer(contract_time, return_type=str),
    WithJsonSchema({"type": "string", "format": "date-time"}),
]


# --- Errors: what app.errors answers (§11.1), described here for OpenAPI --------------------


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ErrorBody(ContractModel):
    error: ErrorDetail


# --- GET /api/farm: layout.json (§2.4) and features.json (§2.5) --------------------------

# Where a zone's readings and controller come from (§2.4): real hardware, or the simulator.
ZoneSource = Literal["LIVE", "SIMULATED"]


class PackModel(ContractModel):
    """A part of the config pack. Anything the model doesn't name goes through as it is."""

    model_config = ConfigDict(extra="allow")


class Reservoir(PackModel):
    """The zone's tank: its full size (volumeL, T1-38) and what can be drawn from it (usableL)."""

    id: str
    volume_l: float | None = None
    usable_l: float


class DayTank(PackModel):
    usable_l: float


class Bench(PackModel):
    id: str
    channels: int
    length_m: float


class GrowingUnit(PackModel):
    """One kind of growing unit in a zone (T1-38): its type, how many, and the plant sites on each.

    The size keys follow the type (channels and lengthM for an NFT_BENCH, volumeL for a bucket …),
    so they go through as written.
    """

    type: str
    count: int
    plant_sites_each: int


class ZoneSize(PackModel):
    length_m: float
    width_m: float


class ZonePosition(PackModel):
    """Where the zone is: metres from the site's corner, and how the farm describes the spot."""

    x_m: float
    y_m: float
    label: str | None = None


class FarmZone(PackModel):
    id: str
    name: str
    source: ZoneSource  # read_farm fills it in when layout.json leaves it out (§2.4)
    controller: str
    growing_system: str | None = None  # NFT, DWC … (T1-38); None in a pack from before it
    growing_units: list[GrowingUnit] | None = None
    size: ZoneSize | None = None
    position: ZonePosition | None = None
    notes: str | None = None
    reservoir: Reservoir
    dosing_cabinet: str
    dose_lines: list[str]
    sensors: dict[str, bool] | None = None  # each sensor, fitted or not
    day_tanks: dict[str, DayTank]
    benches: list[Bench]


class Nursery(PackModel):
    tables: int


class FarmClimate(PackModel):
    controller: str
    source: ZoneSource  # LIVE or SIMULATED, as a zone's; read_farm fills it in when left out (§2.4)
    fans: list[str]
    pad_pumps: list[str]
    chillers: dict[str, str]
    shade: list[str]
    haf: list[str]
    foggers: list[str]
    air_sensors: list[str]


class FarmPower(PackModel):
    monitor: str
    generator: bool
    ups: bool


class FarmDevice(PackModel):
    id: str
    kind: str
    zone: str | None = None


class Farm(PackModel):
    v: int
    site: str
    name: str
    timezone: str
    zones: list[FarmZone]
    nursery: Nursery
    climate: FarmClimate
    power: FarmPower
    devices: list[FarmDevice]
    features: dict[str, bool]


# --- GET /api/zones and /api/zones/{zoneId} (§11.2) -------------------------------------


class ZoneProfile(ContractModel):
    """What the zone is, from the config pack (T1-38): never from the farm's messages."""

    name: str
    growing_system: str | None
    growing_units: list[GrowingUnit]
    plant_sites: int | None  # every unit's plant sites added up; None without growing units
    size: ZoneSize | None
    position: ZonePosition | None
    tank: Reservoir  # layout.json reservoir
    sensors: dict[str, bool]  # each sensor, fitted (true) or not
    controller: str
    link: str  # how the controller reaches the back end: MQTT, or SF_GATEWAY on the rig (io_map.json)
    dose_lines: list[str]
    notes: str | None


class ZoneCrop(ContractModel):
    """What the zone is growing (recipes.json zones, T1-38)."""

    crop: str  # the recipe's crop ID, e.g. LETTUCE
    name: str | None  # its name, e.g. Lettuce
    variety: str | None
    stage: str
    planted_on: date | None  # the farm's own calendar day, YYYY-MM-DD


class ZoneStatus(ContractModel):
    """A zone's latest readings and state. Anything not known yet is null, {} or [] (§11.2)."""

    zone: str
    source: ZoneSource  # LIVE or SIMULATED, from the layout (§2.4)
    online: bool
    last_seen: ApiTime | None
    ts: ApiTime | None
    sim_ts: ApiTime | None = None  # left out unless the latest reading has it (playground sites)
    readings: ZoneReadings
    quality: dict[str, Quality]
    equipment: dict[str, ZoneOutput]
    mode: ZoneMode | None
    estop: StrictBool | None
    leak: StrictBool | None
    stopped: StrictBool | None
    active_cmd_id: UUID4 | None
    operation: ZoneOperation | None
    latches: list[str]
    config_version: Count | None
    # From the config pack, not the farm (T1-38). Set on every GET /zones answer; crop is null
    # when no recipe is applied to the zone.
    profile: ZoneProfile | None = None
    crop: ZoneCrop | None = None


class ZoneList(ContractModel):
    items: list[ZoneStatus]


# --- GET /api/climate and /api/power (§11.2) ---------------------------------------------


class ClimateStatus(ContractModel):
    """The climate controller's latest readings and CLIMATE_STATE, as the zone answer (§11.2)."""

    online: bool
    last_seen: ApiTime | None
    ts: ApiTime | None
    sim_ts: ApiTime | None = None  # left out unless the latest reading has it (playground sites)
    readings: ClimateReadings
    quality: dict[str, Quality]
    mode: ClimateMode | None
    manual_until: ApiTime | None
    stopped: StrictBool | None
    equipment: dict[str, Count | ClimateOutput | ChillerState]  # fanStage and the layout's IDs
    latches: list[str]


class PowerStatus(ContractModel):
    """The power monitor's latest POWER_STATE (§11.2).

    generator and ups are left out when the layout has none, so they are only set when it has.
    """

    online: bool
    last_seen: ApiTime | None
    ts: ApiTime | None
    sim_ts: ApiTime | None = None  # left out unless the latest POWER_STATE has it
    mains: Mains | None
    phases: Phases
    source: PowerSource | None
    generator: GeneratorState | None = None
    ats: AtsPosition | None
    ups: UpsState | None = None
    quality: dict[str, Quality]


# --- POST /api/dose-requests (§11.3) ----------------------------------------------------


class DoseLineAmount(ContractModel):
    line: str
    ml: Number  # whole mL (§1); the review answers 422 INVALID_COMMAND otherwise, as the app's fake


class DoseLineMl(ContractModel):
    """A line's amount in the answer, once checked to be whole mL."""

    line: str
    ml: int


class DoseRequestBody(ContractModel):
    zone: str
    lines: list[DoseLineAmount]


class DoseCheck(ContractModel):
    id: str  # STATE_KNOWN, DEVICES_ONLINE … in the §11.3 order
    result: Literal["PASS", "WARN", "BLOCK"]
    message: str


class Projected(ContractModel):
    ec: float | None
    ph: float | None


class DoseEstimate(ContractModel):
    duration_min: int
    total_ml: int


class DoseReview(ContractModel):
    """The review of one dose request. canConfirm is false if any check is BLOCK."""

    id: str
    zone: str
    lines: list[DoseLineMl]
    projected: Projected
    checks: list[DoseCheck]
    estimate: DoseEstimate
    can_confirm: bool


# --- GET /api/zones/{zoneId}/dose-limits (§11.2) ------------------------------------------


class Drum(ContractModel):
    id: str
    volume_l: float


class DoseLineLimit(ContractModel):
    """One line's limits and how much of its 24 h limit is left, so the app can warn while typing,
    and what the line doses (nutrients.json) and how fast its pump runs (hardware.json), T1-38.
    A pack that doesn't say gives null."""

    line: str
    name: str | None
    compound: str | None
    strength_pct: float | None
    drum: Drum | None
    purpose: str | None
    pump_ml_per_min: float | None
    max_per_dose_ml: float
    # named here: the camelCase generator would make "max24HMl" of these
    max24h_ml: float = Field(alias="max24hMl")
    dosed24h_ml: float = Field(alias="dosed24hMl")
    left24h_ml: float = Field(alias="left24hMl")


class DoseLimits(ContractModel):
    zone: str
    lines: list[DoseLineLimit]


# --- GET /api/devices (§11.2; the fields follow device_state, §12) ------------------------


class AlertInfo(ContractModel):
    """An alert (§11.2): the §12 alert fields, and when it was raised, cleared and acknowledged."""

    id: str
    kind: str
    severity: str
    status: str  # OPEN, ACKNOWLEDGED (seen, condition still on) or CLEARED (condition over)
    zone: str | None
    message: str
    system_action: str
    next_step: str
    raised_at: ApiTime
    sim_ts: ApiTime | None
    cleared_at: ApiTime | None
    acknowledged_at: ApiTime | None
    acknowledged_by: str | None


class AlertList(ContractModel):
    items: list[AlertInfo]


# --- GET /api/operations (§11.2, T1-28) ------------------------------------------------------


class OperationBy(ContractModel):
    """Who did it: the user's ID, their name, and their role when they did it."""

    id: str
    name: str | None  # null for a user the database doesn't have (any more)
    role: str | None  # OWNER, AGRONOMIST, OPERATOR or VIEWER; null if it wasn't recorded


class OperationResult(ContractModel):
    """What came of it: a command's state as GET /commands/{cmdId} shows it, or ACKNOWLEDGED."""

    state: str
    reason: str | None


class OperationAlert(ContractModel):
    """The alert an ACK was for."""

    id: str
    kind: str
    severity: str
    message: str


class Operation(ContractModel):
    """One action a person took (§11.2): a dose, a stop, a resume or an alert acknowledgement."""

    kind: Literal["DOSE", "STOP", "RESUME", "ACK"]
    at: ApiTime  # when the person did it: the command's issuedAt, or when the alert was acknowledged
    by: OperationBy | None  # null for a command sent before sign-in existed
    zone: str | None  # null for a site-wide stop, a climate resume or an alert of no zone
    target: dict[str, Any] | None  # the command's target as sent (§7); null for an ACK
    cmd_id: str | None  # DOSE, STOP and RESUME
    result: OperationResult
    lines: list[CommandLine] | None  # DOSE only: requested and delivered mL per line
    before: EcPh | None  # DOSE only: EC and pH when the first line started
    after: EcPh | None  # DOSE only: EC and pH after the mix wait
    alert: OperationAlert | None  # ACK only


class OperationList(ContractModel):
    items: list[Operation]
    # The next, older page: pass it back as `before`. Microseconds, so no action in the same
    # millisecond as the last one listed is skipped. Null when this page was the last.
    next: str | None


class DeviceInfo(ContractModel):
    device_id: str
    kind: str
    zone: str | None
    online: bool
    last_seen: ApiTime | None


class DeviceList(ContractModel):
    items: list[DeviceInfo]
    next_cursor: str | None
