"""Farm → cloud messages (contract §6): ZONE_TELEMETRY, CLIMATE_TELEMETRY, POWER_STATE,
DEVICE_STATUS, CMD_STATE, ZONE_STATE and CLIMATE_STATE.

Maps keyed by equipment IDs, dose lines or sensor positions are plain dicts, because
those keys come from the client's layout config (contract §2.4, OPEN-12), never from code.

Each telemetry reading is checked against its quality (§6.1): basic physical limits (pH 0–14,
percentages 0–100, EC, volumes and flows ≥ 0) apply when quality is OK, STALE or UNCALIBRATED;
OUT_OF_RANGE carries the raw value or null; FAULT is null; and OK is never null.
"""

import math
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import UUID4, ConfigDict, StrictBool, StrictInt, model_validator

from .envelope import (
    ContractModel,
    Count,
    DeviceId,
    Envelope,
    Number,
    Percent,
    Reading,
    UtcTime,
    Version,
    ZoneId,
    exact_type,
)

Quality = Literal["OK", "STALE", "OUT_OF_RANGE", "FAULT", "UNCALIBRATED"]
ZoneMode = Literal["REMOTE", "LOCAL", "MAINT"]
ClimateMode = Literal["AUTOMATIC", "MANUAL"]
ZoneOutput = Literal["ON", "OFF", "OPEN", "CLOSED"]  # pumps ON/OFF, valves OPEN/CLOSED (§6.1)
ClimateOutput = Literal["ON", "OFF", "OPEN", "CLOSED", "NOT_INSTALLED"]  # shade OPEN/CLOSED, FOG1 (§2.3, §6.2)
Mains = Literal["OK", "LOST", "PHASE_FAULT"]  # §6.3
PowerSource = Literal["MAINS", "GENERATOR", "UPS_ONLY"]
GeneratorState = Literal["STOPPED", "STARTING", "RUNNING", "FAULT"]
AtsPosition = Literal["MAINS", "GENERATOR"]
CmdState = Literal["ACCEPTED", "EXECUTING", "COMPLETED", "PARTIAL", "FAILED", "STOPPED", "REJECTED"]  # §6.6


@dataclass(frozen=True)
class Limits:
    """Basic physical limits of a telemetry reading, attached to its field with Annotated.

    Pydantic doesn't check them by itself: they hold only while the reading's quality says
    the value is a real measurement (§6.1), so the telemetry models check them against quality.
    """

    low: float
    high: float = math.inf

    def __str__(self) -> str:
        return f"between {self.low:g} and {self.high:g}" if self.high < math.inf else f">= {self.low:g}"


PH_SCALE = Limits(0, 14)
PERCENT = Limits(0, 100)
NON_NEGATIVE = Limits(0)  # EC, volumes and flows


def _check_readings_against_quality(readings: ContractModel, quality: dict[str, Quality]) -> None:
    """Check each reading against its quality (§1, §6.1).

    - OK, STALE, UNCALIBRATED or no quality given: within the reading's physical limits.
    - OUT_OF_RANGE: the raw value, even outside the limits (reservoir 101 %), or null.
    - FAULT: null.
    - OK: never null, because a null reading means the sensor is missing or failed.

    `quality` is keyed by reading name; for a map reading (dayTankPct …) it covers every entry.
    A quality key that names no reading is ignored, like any unknown field.
    """
    problems = []
    for name, field in type(readings).model_fields.items():
        key = field.alias or name
        state = quality.get(key)
        value = getattr(readings, name)
        values = list(value.values()) if isinstance(value, dict) else [value]
        limits = next((m for m in field.metadata if isinstance(m, Limits)), None)
        if state == "OK" and None in values:
            problems.append(f"{key} is null, so its quality can't be OK")
        elif state == "FAULT" and any(v is not None for v in values):
            problems.append(f"{key} must be null when its quality is FAULT")
        elif (
            state != "OUT_OF_RANGE"
            and limits
            and any(v is not None and not limits.low <= v <= limits.high for v in values)
        ):
            problems.append(f"{key} must be {limits} when its quality is {state or 'not given'}")
    if problems:
        raise ValueError("; ".join(problems))


# --- 6.1 Zone telemetry ---------------------------------------------------------------


class ZoneReadings(ContractModel):
    ec: Annotated[Reading, NON_NEGATIVE]
    ph: Annotated[Reading, PH_SCALE]
    sol_temp_c: Reading
    reservoir_pct: Annotated[Reading, PERCENT]
    reservoir_l: Annotated[Reading, NON_NEGATIVE]
    circ_flow_lpm: Annotated[Reading, NON_NEGATIVE]
    do_mg_l: Reading  # null unless the zone's config says the sensor is fitted
    day_tank_pct: Annotated[dict[str, Reading], PERCENT]  # keyed by the zone's doseLines
    day_tank_l: Annotated[dict[str, Reading], NON_NEGATIVE]


class ZoneTelemetry(Envelope):
    type: Literal["ZONE_TELEMETRY"]
    zone: ZoneId
    readings: ZoneReadings
    quality: dict[str, Quality]  # keyed by reading name
    equipment: dict[str, ZoneOutput]  # keyed by equipment ID, e.g. Z1-CIRC
    mode: ZoneMode
    estop: StrictBool
    leak: StrictBool
    active_cmd_id: UUID4 | None

    @model_validator(mode="after")
    def _readings_match_quality(self) -> "ZoneTelemetry":
        _check_readings_against_quality(self.readings, self.quality)
        return self


# --- 6.2 Climate telemetry ------------------------------------------------------------


class ClimateReadings(ContractModel):
    air_temp_c: Reading  # average of the positions
    air_temp_by_pos_c: dict[str, Reading]  # keyed by the layout's climate.airSensors
    air_rh: Annotated[Reading, PERCENT]
    outside_temp_c: Reading
    outside_rh: Annotated[Reading, PERCENT]
    elec_room_temp_c: Reading


class ChillerState(ContractModel):
    state: Literal["ON", "OFF"]
    setpoint_c: Number


class ClimateEquipment(ContractModel):
    """`fanStage`, plus one entry per climate equipment ID in the layout (FAN1, PAD1, CH1 …)."""

    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, ClimateOutput | ChillerState]

    fan_stage: Count

    @model_validator(mode="before")
    @classmethod
    def _ignore_unknown_fields(cls, data: Any) -> Any:
        # Equipment IDs are upper case (§2.3) and are checked as extras; a lowerCamelCase
        # key is a field, and unknown fields are ignored (§1).
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k in ("fanStage", "fan_stage") or not k[:1].islower()}
        return data


class ClimateSetpoints(ContractModel):
    air_temp_max_c: Number
    air_rh_max: Percent
    sol_temp_max_c: Number


class ClimateTelemetry(Envelope):
    type: Literal["CLIMATE_TELEMETRY"]
    readings: ClimateReadings
    quality: dict[str, Quality]
    mode: ClimateMode
    manual_until: UtcTime | None  # in MANUAL, when it reverts to AUTOMATIC by itself
    equipment: ClimateEquipment
    run_time_today_min: dict[str, Number]  # keyed by equipment ID or group, e.g. FANS
    setpoints: ClimateSetpoints

    @model_validator(mode="after")
    def _readings_match_quality(self) -> "ClimateTelemetry":
        _check_readings_against_quality(self.readings, self.quality)
        return self


# --- 6.3 Power state ------------------------------------------------------------------


class Phases(ContractModel):
    l1_v: Reading
    l2_v: Reading
    l3_v: Reading


class UpsState(ContractModel):
    on_battery: StrictBool
    battery_pct: Percent | None
    runtime_min: Reading


class PowerState(Envelope):
    type: Literal["POWER_STATE"]
    mains: Mains
    phases: Phases
    source: PowerSource
    # generator and ups are left out when the site's layout.json has
    # "generator": false or "ups": false (§6.3)
    generator: GeneratorState | None = None
    ats: AtsPosition
    ups: UpsState | None = None


# --- 6.4 Device status ----------------------------------------------------------------


class DeviceStatus(Envelope):
    """The heartbeat, every 30 s.

    fwHash, rssiDbm, configVersion and freeHeapKb are null when the device can't report
    them: a simulated or wired device has no radio, heap or firmware build, and there is
    no configVersion before the first DEVICE_CONFIG (contract §1: null, never a fake value).
    """

    type: Literal["DEVICE_STATUS"]
    online: StrictBool
    fw: str
    fw_hash: str | None
    uptime_s: Count
    rssi_dbm: StrictInt | None
    clock_synced: StrictBool
    config_version: Count | None
    free_heap_kb: Count | None
    # GW1 only (contract §10)
    buffered_messages: Count | None = None
    cloud_link: Literal["UP", "DOWN"] | None = None


class DeviceStatusLastWill(ContractModel):
    """The last will on the status topic (§6.4). It carries only these four fields."""

    v: Version
    type: Literal["DEVICE_STATUS"]
    device: DeviceId
    online: Annotated[Literal[False], exact_type(bool)]


# --- 6.6 Command state ----------------------------------------------------------------


class CommandLine(ContractModel):
    line: str
    requested_ml: Count
    delivered_ml: Count
    state: Literal["PENDING", "RUNNING", "DONE", "SHORT", "SKIPPED"]


class EcPh(ContractModel):
    ec: Number | None
    ph: Number | None


STATES_WITH_A_REASON = frozenset({"PARTIAL", "FAILED", "STOPPED", "REJECTED"})  # §6.6


class CommandState(Envelope):
    """A controller's report on one command it received (§6.6, §8)."""

    type: Literal["CMD_STATE"]
    cmd_id: UUID4
    state: CmdState
    reason: str | None  # a reason code (§8.4), exactly when the state is in STATES_WITH_A_REASON
    lines: list[CommandLine] | None = None  # DOSE only
    before: EcPh | None  # null until the first line starts, and on REJECTED
    after: EcPh | None  # null until the mix wait is over
    started_at: UtcTime | None
    ended_at: UtcTime | None
    detail: dict[str, Any] | None = None  # refill and climate commands only

    @model_validator(mode="after")
    def _reason_only_where_6_6_gives_one(self) -> "CommandState":
        if (self.reason is not None) != (self.state in STATES_WITH_A_REASON):
            raise ValueError(f"a {self.state} CMD_STATE {'needs' if self.reason is None else 'has no'} reason")
        return self


# --- 6.8 Zone state -------------------------------------------------------------------


class ZoneOperation(ContractModel):
    cmd_id: UUID4
    kind: Literal["DOSE", "REFILL"]
    state: CmdState
    line: str | None = None  # the dose line running now; DOSE only


class ZoneState(Envelope):
    type: Literal["ZONE_STATE"]
    zone: ZoneId
    mode: ZoneMode
    estop: StrictBool
    stopped: StrictBool  # true until a person sends RESUME (§7.8)
    outputs: dict[str, ZoneOutput]  # keyed by equipment ID
    operation: ZoneOperation | None  # the running dose or refill
    latches: list[str]  # faults locked out until CLEAR_FAULT, e.g. LOW_LOW, LEAK
    config_version: Count | None  # null before the first DEVICE_CONFIG, as in DEVICE_STATUS


# --- 6.9 Climate state ----------------------------------------------------------------


class ClimateState(Envelope):
    type: Literal["CLIMATE_STATE"]
    mode: ClimateMode
    manual_until: UtcTime | None  # in MANUAL, when it reverts to AUTOMATIC by itself
    stopped: StrictBool  # true until a person sends RESUME (§7.8)
    equipment: ClimateEquipment  # as in §6.2
    latches: list[str]  # faults locked out until CLEAR_FAULT


FARM_MESSAGE_MODELS: dict[str, type[Envelope]] = {
    "ZONE_TELEMETRY": ZoneTelemetry,
    "CLIMATE_TELEMETRY": ClimateTelemetry,
    "POWER_STATE": PowerState,
    "DEVICE_STATUS": DeviceStatus,
    "CMD_STATE": CommandState,
    "ZONE_STATE": ZoneState,
    "CLIMATE_STATE": ClimateState,
}


def parse_farm_message(payload: dict[str, Any]) -> Envelope | DeviceStatusLastWill:
    """Check one decoded farm → cloud message against its contract model.

    Raises pydantic.ValidationError if it breaks the contract, and ValueError if its
    type has no model here yet.
    """
    kind = payload.get("type")
    if kind == "DEVICE_STATUS" and "msgId" not in payload:
        return DeviceStatusLastWill.model_validate(payload)
    model = FARM_MESSAGE_MODELS.get(kind)
    if model is None:
        raise ValueError(f"no contract model for message type {kind!r}")
    return model.model_validate(payload)
