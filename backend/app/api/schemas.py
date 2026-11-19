"""What the read API answers (contract §2.4, §11.2), and so what /api/openapi.json describes.

The models come from the contract models, so wire names stay lowerCamelCase. Maps keyed by zones,
dose lines or equipment IDs are plain dicts, because those keys come from the client's config
(OPEN-12), and fields the models don't name in the config pack are passed on unchanged.
"""

from datetime import UTC, datetime
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
    id: str
    usable_l: float


class DayTank(PackModel):
    usable_l: float


class Bench(PackModel):
    id: str
    channels: int
    length_m: float


class FarmZone(PackModel):
    id: str
    name: str
    source: ZoneSource  # read_farm fills it in when layout.json leaves it out (§2.4)
    controller: str
    reservoir: Reservoir
    dosing_cabinet: str
    dose_lines: list[str]
    sensors: dict[str, bool] | None = None
    day_tanks: dict[str, DayTank]
    benches: list[Bench]


class Nursery(PackModel):
    tables: int


class FarmClimate(PackModel):
    controller: str
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


class DoseLineLimit(ContractModel):
    """One line's limits and how much of its 24 h limit is left, so the app can warn while typing."""

    line: str
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


class DeviceInfo(ContractModel):
    device_id: str
    kind: str
    zone: str | None
    online: bool
    last_seen: ApiTime | None


class DeviceList(ContractModel):
    items: list[DeviceInfo]
    next_cursor: str | None
