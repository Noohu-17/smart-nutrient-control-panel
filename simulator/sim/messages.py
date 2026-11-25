"""Contract messages: the envelope (section 5), DEVICE_STATUS (6.4), the readings (6.1-6.3)
and the retained controller state (6.8, 6.9).

Messages are plain dicts built here, field for field as the contract says. The tests check
them against Track 1's models (backend/app/contract).

The reading functions are also where a true value becomes what a sensor reports: rounded to
the precision in contract section 1, or null with its quality when there is nothing to measure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .farm import ClimateFarm, PowerFarm, ZoneFarm

SIM_FIRMWARE = "sim-0.1.0"
# A stuck probe's quality. Contract 6.1 has no STUCK; STALE is "no fresh sample", which is what a
# stuck probe gives, and like any quality but OK it is never used for dosing.
STUCK_QUALITY = "STALE"


def utc_ts(moment: datetime) -> str:
    """Contract time: ISO-8601 UTC with milliseconds and Z, e.g. 2026-11-09T04:54:00.000Z."""
    moment = moment.astimezone(UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def status_topic(site: str, device_id: str) -> str:
    return f"farm/{site}/device/{device_id}/status"


def zone_telemetry_topic(site: str, zone_id: str) -> str:
    return f"farm/{site}/zone/{zone_id}/telemetry"


def climate_telemetry_topic(site: str) -> str:
    return f"farm/{site}/climate/telemetry"


def power_state_topic(site: str) -> str:
    return f"farm/{site}/power/state"


def zone_state_topic(site: str, zone_id: str) -> str:
    return f"farm/{site}/zone/{zone_id}/state"


def climate_state_topic(site: str) -> str:
    return f"farm/{site}/climate/state"


def zone_cmd_topic(site: str, zone_id: str) -> str:
    return f"farm/{site}/zone/{zone_id}/cmd"


def zone_cmd_state_topic(site: str, zone_id: str) -> str:
    return f"farm/{site}/zone/{zone_id}/cmd/state"


def zone_event_topic(site: str, zone_id: str) -> str:
    return f"farm/{site}/zone/{zone_id}/event"


def stop_topic(site: str) -> str:
    return f"farm/{site}/stop"


class Envelope:
    """Header for every message one device sends during one boot (contract 5).

    seq counts every message of this boot, whatever its type; (bootId, seq)
    gives the back end exact order and shows gaps.
    """

    def __init__(self, site: str, device_id: str, boot_id: int):
        self.site, self.device_id, self.boot_id = site, device_id, boot_id
        self.seq = 0

    def next(self, msg_type: str, ts: datetime, sim_ts: datetime | None = None) -> dict[str, Any]:
        """ts is real time; sim_ts (playground sites only) becomes simTs."""
        self.seq += 1
        header = {
            "v": 1,
            "type": msg_type,
            "msgId": str(uuid.uuid4()),
            "ts": utc_ts(ts),
            "site": self.site,
            "device": self.device_id,
            "bootId": self.boot_id,
            "seq": self.seq,
        }
        if sim_ts is not None:
            header["simTs"] = utc_ts(sim_ts)
        return header


def device_status(envelope: dict[str, Any], *, uptime_s: int, gateway: bool) -> dict[str, Any]:
    """DEVICE_STATUS (6.4) for a simulated device.

    A simulated device has no radio, heap or firmware build, so those fields
    are null rather than invented numbers (contract 1: never a fake value).
    configVersion stays null until DEVICE_CONFIG (7.7) is handled. GW1 also
    reports its buffer and cloud link (10, rule 5).
    """
    status = {
        **envelope,
        "online": True,
        "fw": SIM_FIRMWARE,
        "fwHash": None,
        "uptimeS": uptime_s,
        "rssiDbm": None,
        "clockSynced": True,
        "configVersion": None,
        "freeHeapKb": None,
    }
    if gateway:
        status.update(bufferedMessages=0, cloudLink="UP")
    return status


def offline_status(device_id: str) -> dict[str, Any]:
    """The last-will payload, exactly as contract 6.4 gives it."""
    return {"v": 1, "type": "DEVICE_STATUS", "device": device_id, "online": False}


def zone_telemetry(zone: ZoneFarm) -> dict[str, Any]:
    """ZONE_TELEMETRY (6.1) without its envelope.

    doMgL stays null: the farm model has no dissolved oxygen, so a fitted sensor has nothing
    to report and says so with its quality; an unfitted one has no quality at all.
    """
    water = zone.water()
    readings = {
        "ec": _rounded(water.ec_ms_cm, 2),
        "ph": _rounded(water.ph, 2),
        "solTempC": _rounded(water.temp_c, 1),
        "reservoirPct": _rounded(water.level_pct, 0),
        "reservoirL": _rounded(water.volume_l, 0),
        "circFlowLpm": _rounded(zone.circ_flow_lpm, 1),
        "doMgL": None,
        "dayTankPct": {
            line: _rounded(100.0 * litres / zone.day_tank_usable_l[line], 0)
            for line, litres in zone.day_tank_l.items()
        },
        "dayTankL": {line: _rounded(litres, 1) for line, litres in zone.day_tank_l.items()},
    }
    readings.update(zone.stuck_readings)  # a stuck probe repeats what it last read
    for name in zone.sensor_faults:  # a probe that stopped answering has nothing to report
        readings[name] = None
    sensors = ("ec", "ph", "solTempC", "reservoirPct", "circFlowLpm")
    quality = {name: _quality(readings[name]) for name in sensors}
    if zone.do_fitted:
        quality["doMgL"] = _quality(readings["doMgL"])
    for name in zone.stuck_readings:
        if name in quality and readings[name] is not None:
            quality[name] = STUCK_QUALITY
    return {
        "zone": zone.zone_id,
        "readings": readings,
        "quality": quality,
        "equipment": dict(zone.outputs),
        "mode": zone.mode,
        "estop": zone.estop,
        "leak": zone.leak,
        "activeCmdId": zone.active_cmd_id,
    }


def climate_telemetry(climate: ClimateFarm) -> dict[str, Any]:
    """CLIMATE_TELEMETRY (6.2) without its envelope."""
    air = climate.air
    readings = {
        "airTempC": _rounded(air.temp_c, 1),
        "airTempByPosC": {pos: _rounded(t, 1) for pos, t in air.temp_by_pos_c.items()},
        "airRh": _rounded(air.rh, 0),
        "outsideTempC": _rounded(air.outside_temp_c, 1),
        "outsideRh": _rounded(air.outside_rh, 0),
        "elecRoomTempC": _rounded(air.elec_room_temp_c, 1),
    }
    sensors = ("airTempC", "airRh", "outsideTempC", "outsideRh", "elecRoomTempC")
    until = climate.manual_until
    return {
        "readings": readings,
        "quality": {name: _quality(readings[name]) for name in sensors},
        "mode": climate.mode,
        "manualUntil": utc_ts(until) if until else None,
        "equipment": climate.equipment(),
        "runTimeTodayMin": climate.run_time_today_min(),
        "setpoints": dict(climate.setpoints),
    }


def power_state(power: PowerFarm) -> dict[str, Any]:
    """POWER_STATE (6.3) without its envelope; no generator or UPS in the layout, no field."""
    state: dict[str, Any] = {
        "mains": power.mains,
        "phases": {phase: _rounded(volts, 0) for phase, volts in power.phases_v.items()},
        "source": power.source,
    }
    if power.generator is not None:
        state["generator"] = power.generator
    state["ats"] = power.ats
    if power.ups_battery_pct is not None:
        state["ups"] = {
            "onBattery": power.ups_on_battery,
            "batteryPct": _rounded(power.ups_battery_pct, 0),
            "runtimeMin": _rounded(power.ups_runtime_min, 0),
        }
    return state


def zone_state(zone: ZoneFarm) -> dict[str, Any]:
    """ZONE_STATE (6.8) without its envelope: equal bodies mean nothing meaningful changed."""
    return {
        "zone": zone.zone_id,
        "mode": zone.mode,
        "estop": zone.estop,
        "stopped": zone.stopped,
        "outputs": dict(zone.outputs),
        "operation": dict(zone.operation) if zone.operation else None,
        "latches": sorted(zone.latches),
        "configVersion": zone.config_version,
    }


def climate_state(climate: ClimateFarm) -> dict[str, Any]:
    """CLIMATE_STATE (6.9) without its envelope. No readings or run time: those are telemetry."""
    until = climate.manual_until
    return {
        "mode": climate.mode,
        "manualUntil": utc_ts(until) if until else None,
        "stopped": climate.stopped,
        "equipment": climate.equipment(),
        "latches": sorted(climate.latches),
    }


def _rounded(value: float | None, digits: int) -> float | int | None:
    """A reading at its contract precision (section 1); whole units go out as whole numbers."""
    if value is None:
        return None
    return round(value, digits) if digits else round(value)


def _quality(reading: float | None) -> str:
    """No value means no measurement: null with FAULT, never a fake 0 (contract 1, 6.1)."""
    return "FAULT" if reading is None else "OK"
