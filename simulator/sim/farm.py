"""The simulated farm's live state, built from the config pack: what the devices report.

Only physics moves it, through Farm.step(): the reservoirs follow the Track 3 model, a running
dosing pump moves stock from its day-tank into the reservoir at its rate in hardware.json, where
circulation mixes it in over time (simulator.json model.mixing), and the climate's run-time
counters add up. Air and power are held constant until a weather model and the
power drills exist. Nothing here switches equipment: outputs change only when a controller, acting
on a person's command, sets them (contract 0, "Phase A is manual").
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import load_demo_start, pump_rate, read_pack_file
from .model.model_config import (
    AirConditions,
    PowerSupply,
    initial_reservoir,
    parse_air,
    parse_chiller_setpoint,
    parse_circulation_flow_lpm,
    parse_day_tanks,
    parse_power,
    parse_zone_model,
)
from .model.nutrients import ML_PER_L
from .model.params import ReservoirParams, require_finite
from .model.water import (
    ReservoirReadings,
    ReservoirState,
    StepInputs,
    dose,
    readings,
    refill_flow_lps,
    step_reservoir,
)

FANS_GROUP = "FANS"  # contract 6.2 runTimeTodayMin: all the fans count as one
MIXED_ML = 1e-3  # stock left unmixed below this is mixed in at once
RESERVED_FOGGER = "FOG1"  # contract 2.3: reported as not installed, so the app can say so
SHADE_START = "OPEN"  # where a new farm's screens are, as in contract 6.2; a scenario moves them
SETPOINTS = ("airTempMaxC", "airRhMax", "solTempMaxC")  # contract 6.2


@dataclass
class ZoneFarm:
    """One zone: its water and day-tanks, and what its panel and controller show."""

    zone_id: str
    params: ReservoirParams
    reservoir: ReservoirState
    day_tank_l: dict[str, float]
    day_tank_usable_l: Mapping[str, float]
    pump_flow_lpm: float
    do_fitted: bool
    outputs: dict[str, str]
    mode: str = "REMOTE"  # the key switch on the panel (contract 6.1)
    estop: bool = False
    leak: bool = False
    stopped: bool = False  # a new farm is not stopped; a simulated reboot will set it (6.8)
    operation: dict[str, Any] | None = None  # the running dose or refill (6.8)
    latches: set[str] = field(default_factory=set)
    config_version: int | None = None  # null until the first DEVICE_CONFIG (7.7)
    name: str | None = None  # what people call it (layout.json), for event wording
    dose_pump_ml_per_s: Mapping[str, float] = field(default_factory=dict)  # hardware.json
    flow_meter_ml: dict[str, float] = field(default_factory=dict)  # counted since last read
    sensor_faults: set[str] = field(default_factory=set)  # probes that stopped answering (11.4.3)
    stuck_readings: dict[str, float | None] = field(default_factory=dict)  # stuck probes' values
    mix_time_constant_s: float = 0.0  # simulator.json model.mixing; 0 mixes at once
    unmixed_ml: dict[str, float] = field(default_factory=dict)  # stock not yet through the water

    @property
    def active_cmd_id(self) -> str | None:
        """The command running now (6.1): the same one ZONE_STATE reports."""
        return self.operation["cmdId"] if self.operation else None

    @property
    def circ_flow_lpm(self) -> float:
        """A pump that isn't running moves nothing: this 0 is a measurement, not a fake."""
        return self.pump_flow_lpm if self.outputs[f"{self.zone_id}-CIRC"] == "ON" else 0.0

    def water(self) -> ReservoirReadings:
        return readings(self.reservoir, self.params)

    def step(self, dt_s: float, air_temp_c: float) -> None:
        self._run_dose_pumps(dt_s)
        valve_open = self.outputs[f"{self.zone_id}-REFILL"] == "OPEN"
        inputs = StepInputs(
            air_temp_c=air_temp_c, refill_lps=refill_flow_lps(self.params) if valve_open else 0.0
        )  # no plants until a scenario plants them (contract 11.4)
        self.reservoir = step_reservoir(self.reservoir, dt_s, inputs, self.params)

    def _run_dose_pumps(self, dt_s: float) -> None:
        """A running pump moves stock from its day-tank into the reservoir, where it mixes in
        over time (_mix): EC really changes, as the probes see it.

        A day-tank that runs dry delivers what it had left, and its flow meter counts only that.
        """
        for line, ml_per_s in self.dose_pump_ml_per_s.items():
            if dt_s <= 0 or self.outputs[f"{self.zone_id}-DOSE-{line}"] != "ON":
                continue
            ml = min(ml_per_s * dt_s, self.day_tank_l[line] * ML_PER_L)
            if ml > 0:
                self.day_tank_l[line] -= ml / ML_PER_L
                self.unmixed_ml[line] = self.unmixed_ml.get(line, 0.0) + ml
                self.flow_meter_ml[line] = self.flow_meter_ml.get(line, 0.0) + ml
        self._mix(dt_s)

    def _mix(self, dt_s: float) -> None:
        """Circulation carries new stock through the reservoir: each step, a share
        1 - exp(-dt / timeConstant) of what is still unmixed reaches the water the probes measure.
        Nothing mixes while circulation is off; a time constant of 0 mixes at once."""
        tau = self.mix_time_constant_s
        if tau > 0 and (dt_s <= 0 or self.outputs[f"{self.zone_id}-CIRC"] != "ON"):
            return
        share = 1.0 if tau <= 0 else -math.expm1(-dt_s / tau)
        for line, ml in self.unmixed_ml.items():
            if ml <= 0:
                continue
            mixed = ml if ml * (1.0 - share) < MIXED_ML else ml * share
            self.reservoir = dose(self.reservoir, line, mixed, self.params)
            self.unmixed_ml[line] = ml - mixed


@dataclass
class ClimateFarm:
    """The polyhouse air and the equipment CC1 owns, from layout.climate."""

    air: AirConditions
    fans: tuple[str, ...]
    pad_pumps: tuple[str, ...]
    outputs: dict[str, str]  # by equipment ID: pads, shade, HAF, foggers
    chillers: dict[str, dict[str, Any]]
    setpoints: Mapping[str, float]
    timezone: ZoneInfo
    mode: str
    manual_until: datetime | None = None
    fan_stage: int = 0
    stopped: bool = False
    latches: set[str] = field(default_factory=set)
    run_time_s: dict[str, float] = field(default_factory=dict)
    run_day: date | None = None

    def equipment(self) -> dict[str, Any]:
        """As contract 6.2: the fan stage, then one entry per piece of equipment."""
        fans = {
            fan: "ON" if position < self.fan_stage else "OFF"
            for position, fan in enumerate(self.fans)
        }
        chillers = {chiller: dict(state) for chiller, state in self.chillers.items()}
        return {"fanStage": self.fan_stage, **fans, **self.outputs, **chillers}

    def run_time_today_min(self) -> dict[str, int]:
        return {name: int(seconds // 60) for name, seconds in self.run_time_s.items()}

    def step(self, dt_s: float, now: datetime) -> None:
        """Count run time; "today" is the farm's own day, in the layout's timezone."""
        local = now.astimezone(self.timezone)
        if local.date() != self.run_day:
            self.run_day = local.date()
            self.run_time_s = dict.fromkeys(self.run_time_s, 0.0)
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            dt_s = min(dt_s, (local - midnight).total_seconds())  # only the new day's part counts
        if self.fan_stage > 0:
            self.run_time_s[FANS_GROUP] += dt_s
        for pad in self.pad_pumps:
            if self.outputs[pad] == "ON":
                self.run_time_s[pad] += dt_s


@dataclass
class PowerFarm:
    """What PM1 monitors. generator and ups are None when the layout says there is none."""

    phases_v: dict[str, float]
    generator: str | None
    ups_battery_pct: float | None
    ups_runtime_min: float | None
    mains: str = "OK"
    source: str = "MAINS"
    ats: str = "MAINS"
    ups_on_battery: bool = False


@dataclass
class Farm:
    zones: dict[str, ZoneFarm]
    climate: ClimateFarm
    power: PowerFarm
    now: datetime | None = None  # simulated time the farm has reached; set at the start

    def step(self, dt_s: float, now: datetime) -> None:
        """Move the farm forward by dt_s simulated seconds, ending at simulated time `now`."""
        air_temp_c = self.climate.air.temp_c
        for zone in self.zones.values():
            zone.step(dt_s, air_temp_c)
        self.climate.step(dt_s, now)
        self.now = now


def load_farm(client_dir: Path) -> Farm:
    """Build the farm in its configured starting state: model.initialState, with each zone's
    demoStart values over it (T4-9). A scenario may override it later."""
    layout = read_pack_file(client_dir, "layout.json")
    model = read_pack_file(client_dir, "simulator.json").get("model")
    if not isinstance(model, Mapping):
        raise ValueError(f"simulator.json: model must be an object, got {model!r}")
    climate_cfg = read_pack_file(client_dir, "climate.json")
    features = read_pack_file(client_dir, "features.json")
    hardware = read_pack_file(client_dir, "hardware.json")
    demo_zones = load_demo_start(client_dir)["zones"]
    zones = [
        _build_zone(_zone_model(model, demo_zones.get(zone.get("id"))), zone, hardware)
        for zone in layout.get("zones", ())
    ]
    return Farm(
        zones={zone.zone_id: zone for zone in zones},
        climate=_build_climate(model, layout, climate_cfg, features),
        power=_build_power(model, layout.get("power") or {}),
    )


def _zone_model(model: Mapping[str, Any], start: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """The model as one zone sees it: its demoStart values over model.initialState."""
    initial = model.get("initialState")
    if not start or not isinstance(initial, Mapping):
        return model  # nothing to change, or an error the model's own checks will report
    merged = {**initial, **start}
    tanks, start_tanks = initial.get("dayTankPct"), start.get("dayTankPct")
    if isinstance(tanks, Mapping) and isinstance(start_tanks, Mapping):
        merged["dayTankPct"] = {**tanks, **start_tanks}
    return {**model, "initialState": merged}


def _build_zone(
    model: Mapping[str, Any], zone: Mapping[str, Any], hardware: Mapping[str, Any]
) -> ZoneFarm:
    params = parse_zone_model(model, zone)
    zone_id = params.zone_id
    tanks = parse_day_tanks(model, zone)
    outputs = {f"{zone_id}-CIRC": "ON", f"{zone_id}-REFILL": "CLOSED"}
    outputs.update({f"{zone_id}-DOSE-{line}": "OFF" for line in tanks})
    return ZoneFarm(
        zone_id=zone_id,
        params=params.reservoir,
        reservoir=initial_reservoir(params),
        day_tank_l={line: tank.start_l for line, tank in tanks.items()},
        day_tank_usable_l={line: tank.usable_l for line, tank in tanks.items()},
        pump_flow_lpm=parse_circulation_flow_lpm(model),
        do_fitted=bool((zone.get("sensors") or {}).get("dissolvedOxygen", False)),
        outputs=outputs,
        name=zone.get("name"),
        dose_pump_ml_per_s=_dose_pump_rates(hardware, zone_id, tanks),
        flow_meter_ml=dict.fromkeys(tanks, 0.0),
        mix_time_constant_s=_mix_time_constant_s(model),
    )


def _mix_time_constant_s(model: Mapping[str, Any]) -> float:
    """How fast new stock mixes through the reservoir (simulator.json model.mixing)."""
    mixing = model.get("mixing")
    if mixing is None:
        return 0.0  # no mixing model: stock reaches the probes at once
    value = mixing.get("timeConstantS") if isinstance(mixing, Mapping) else None
    try:
        require_finite("timeConstantS", value)
    except ValueError:
        value = None
    if value is None or value < 0:
        raise ValueError(
            f"simulator.json: model.mixing.timeConstantS must be a number >= 0, got "
            f"{mixing.get('timeConstantS') if isinstance(mixing, Mapping) else mixing!r}"
        )
    return float(value)


def _dose_pump_rates(
    hardware: Mapping[str, Any], zone_id: str, lines: Iterable[str]
) -> dict[str, float]:
    """Each line's pump flow in this zone (hardware.json, config.pump_rate: the zone's own
    pumps.zones.<zone>.doseLines where it has them, else pumps.doseLines); every installed line
    needs one."""
    rates = {}
    for line in lines:
        written, key = pump_rate(hardware, zone_id, line)
        rate = written
        try:
            require_finite(line, rate)
        except ValueError:
            rate = None
        if rate is None or rate <= 0:
            raise ValueError(f"hardware.json: {key} must be a number > 0, got {written!r}")
        rates[line] = float(rate)
    return rates


def _build_climate(
    model: Mapping[str, Any],
    layout: Mapping[str, Any],
    climate_cfg: Mapping[str, Any],
    features: Mapping[str, Any],
) -> ClimateFarm:
    cfg = layout.get("climate")
    if not isinstance(cfg, Mapping):
        raise ValueError(f"layout.json: climate must be an object, got {cfg!r}")
    pad_pumps = tuple(cfg.get("padPumps", ()))
    foggers = tuple(cfg.get("foggers", ()))
    outputs = {pad: "OFF" for pad in pad_pumps}
    outputs.update({shade: SHADE_START for shade in cfg.get("shade", ())})
    outputs.update({haf: "OFF" for haf in cfg.get("haf", ())})
    outputs.update({fogger: "OFF" for fogger in foggers} or {RESERVED_FOGGER: "NOT_INSTALLED"})
    chiller_ids = tuple((cfg.get("chillers") or {}).values())
    chillers = {}
    if chiller_ids:
        setpoint_c = parse_chiller_setpoint(model, climate_cfg.get("chillerSetpointC") or {})
        chillers = {chiller: {"state": "OFF", "setpointC": setpoint_c} for chiller in chiller_ids}
    return ClimateFarm(
        air=parse_air(model, cfg.get("airSensors")),
        fans=tuple(cfg.get("fans", ())),
        pad_pumps=pad_pumps,
        outputs=outputs,
        chillers=chillers,
        setpoints=_setpoints(climate_cfg),
        timezone=_timezone(layout),
        # Contract 7.5: while climateAutomatic is off, climate stays MANUAL with no time limit.
        mode="AUTOMATIC" if features.get("climateAutomatic") else "MANUAL",
        run_time_s=dict.fromkeys((FANS_GROUP, *pad_pumps), 0.0),
    )


def _build_power(model: Mapping[str, Any], cfg: Mapping[str, Any]) -> PowerFarm:
    supply: PowerSupply = parse_power(model)
    has_ups = bool(cfg.get("ups"))
    return PowerFarm(
        phases_v=dict(supply.phases_v),
        generator="STOPPED" if cfg.get("generator") else None,
        ups_battery_pct=supply.ups_battery_pct if has_ups else None,
        ups_runtime_min=supply.ups_runtime_min if has_ups else None,
    )


def _setpoints(climate_cfg: Mapping[str, Any]) -> dict[str, float]:
    cfg = climate_cfg.get("setpoints") or {}
    for key in SETPOINTS:
        try:
            require_finite(key, cfg.get(key))
        except ValueError:
            raise ValueError(
                f"climate.json: setpoints.{key} must be a number, got {cfg.get(key)!r}"
            ) from None
    return {key: cfg[key] for key in SETPOINTS}


def _timezone(layout: Mapping[str, Any]) -> ZoneInfo:
    name = layout.get("timezone")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError(f"layout.json: timezone must be an IANA name, got {name!r}") from None
