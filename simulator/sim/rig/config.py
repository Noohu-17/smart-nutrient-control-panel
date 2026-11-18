"""Which zones run on the prototype rig, from the config pack, and where the rig's topics are.

A zone controller in io_map.json "controllers" with `"link": "SF_GATEWAY"` is run by this service:

    "ZC1": {
      "link": "SF_GATEWAY",
      "gateway": "gateway-01",              the gateway's client ID (sf documents' "gateway")
      "rigZone": 1,                          its zone number on the rig (sf/zone/<n>)
      "pumps": {"A": "relay1", ...},          dose line -> relay, for every dose line of the zone
      "tdsPpmPerMsCm": 500,                   the TDS meter's scale: EC = ppm / this
      "dayTankLevelSensors": false            the drums have no level sensors (checks.Facts)
    }

Each pump's flow comes from hardware.json pumps.doseLines.<line>.flowMlPerS: a dose of x mL runs
for x / flow seconds, so a line's largest dose (limits.json maxPerDoseMl) must fit in the rig's
600 s. The broker topic bases are settings, not config: RIG_TELEMETRY_TOPIC and
RIG_COMMAND_TOPIC (`m/<workspace>/c/<channel>`, from the gateway's secrets, never in git).
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import load_site, load_zone_dose, load_zone_limits, read_pack_file
from .sf import DOSE_SECONDS

LINK = "SF_GATEWAY"
RELAYS = ("relay1", "relay2", "relay3")  # the rig's dosing pumps; relay4 is the water inlet
GATEWAY_ZONE = 2  # zone 2's pumps are the gateway's own relays; zone 1's are the actuator box's


@dataclass(frozen=True)
class RigZone:
    """One zone whose controller is the rig."""

    device_id: str  # ZC1: the contract device the service plays
    zone_id: str  # Z1
    name: str
    gateway: str
    rig_zone: int
    pumps: Mapping[str, str]  # dose line -> relay
    pump_ml_per_s: Mapping[str, float]  # dose line -> flow
    tds_ppm_per_ms_cm: float
    day_tank_level_sensors: bool
    limits: Mapping[str, Any] = field(default_factory=dict)
    dose: Mapping[str, Any] = field(default_factory=dict)

    @property
    def target(self) -> str:
        """sf/cmd/1 target for this zone's pumps: zone:1 is the actuator box, zone:2 the gateway."""
        return f"zone:{self.rig_zone}"

    @property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.pumps)

    @property
    def pumps_on_gateway(self) -> bool:
        """Zone 2: the pumps are the gateway's relays, so they restart with it."""
        return self.rig_zone == GATEWAY_ZONE


@dataclass(frozen=True)
class RigSettings:
    telemetry_topic: str  # m/<workspace>/c/<telemetry channel>
    command_topic: str  # m/<workspace>/c/<relay-cmd channel>
    link_id: str  # this service's own MQTT login for the rig's topics
    offline_after_s: float


def load_rig_settings(env: Mapping[str, str] = os.environ) -> RigSettings:
    offline_text = env.get("RIG_OFFLINE_S") or "90"
    try:
        offline_s = float(offline_text)
    except ValueError:
        raise ValueError(f"RIG_OFFLINE_S must be a number, got {offline_text!r}") from None
    if not math.isfinite(offline_s) or offline_s <= 0:
        raise ValueError(f"RIG_OFFLINE_S must be more than 0, got {offline_text!r}")
    return RigSettings(
        telemetry_topic=(env.get("RIG_TELEMETRY_TOPIC") or "m/dev/c/telemetry").rstrip("/"),
        command_topic=(env.get("RIG_COMMAND_TOPIC") or "m/dev/c/relay-cmd").rstrip("/"),
        link_id=env.get("RIG_LINK_ID") or "RIGLINK",
        offline_after_s=offline_s,
    )


def load_rig_zones(client_dir: Path) -> list[RigZone]:
    """Every zone io_map.json puts on the rig, checked against the layout and hardware.json."""
    controllers = read_pack_file(client_dir, "io_map.json").get("controllers") or {}
    if not isinstance(controllers, Mapping):
        raise ValueError("io_map.json: controllers must be an object")
    layout = read_pack_file(client_dir, "layout.json")
    zones = {z.get("controller"): z for z in layout.get("zones", ()) if isinstance(z, Mapping)}
    flows = (read_pack_file(client_dir, "hardware.json").get("pumps") or {}).get("doseLines") or {}
    rig_zones = []
    for device_id, entry in controllers.items():
        if not isinstance(entry, Mapping) or entry.get("link") != LINK:
            continue
        rig_zones.append(_rig_zone(client_dir, device_id, entry, zones.get(device_id), flows))
    seen = [(z.gateway, z.rig_zone) for z in rig_zones]
    if len(seen) != len(set(seen)):
        raise ValueError("io_map.json: two controllers are on the same rig zone")
    return rig_zones


def _rig_zone(
    client_dir: Path,
    device_id: str,
    entry: Mapping[str, Any],
    zone: Mapping[str, Any] | None,
    flows: Mapping[str, Any],
) -> RigZone:
    where = f"io_map.json: controllers.{device_id}"
    if zone is None:
        raise ValueError(f"{where}: no layout.json zone has controller {device_id}")
    gateway, rig_zone = entry.get("gateway"), entry.get("rigZone")
    if not isinstance(gateway, str) or not gateway:
        raise ValueError(f"{where}.gateway must be the gateway's client ID, got {gateway!r}")
    if rig_zone not in (1, 2) or isinstance(rig_zone, bool):
        raise ValueError(f"{where}.rigZone must be 1 or 2, got {rig_zone!r}")
    pumps = entry.get("pumps")
    lines = list(zone.get("doseLines") or ())
    if not isinstance(pumps, Mapping) or sorted(pumps) != sorted(lines):
        raise ValueError(f"{where}.pumps must map each dose line {lines} to a relay, got {pumps!r}")
    relays = list(pumps.values())
    if any(relay not in RELAYS for relay in relays) or len(set(relays)) != len(relays):
        raise ValueError(f"{where}.pumps: each line needs its own relay of {list(RELAYS)}")
    ml_per_s = {}
    for line in lines:
        flow = (flows.get(line) or {}).get("flowMlPerS")
        if not _positive(flow):
            raise ValueError(
                f"hardware.json: pumps.doseLines.{line}.flowMlPerS must be a number > 0, "
                f"got {flow!r}"
            )
        ml_per_s[line] = float(flow)
    scale = entry.get("tdsPpmPerMsCm")
    if not _positive(scale):
        raise ValueError(f"{where}.tdsPpmPerMsCm must be a number > 0, got {scale!r}")
    sensors = entry.get("dayTankLevelSensors")
    if not isinstance(sensors, bool):
        raise ValueError(f"{where}.dayTankLevelSensors must be true or false, got {sensors!r}")
    zone_id = zone["id"]
    load_site(client_dir)  # the pack must name its site
    limits = load_zone_limits(client_dir, zone_id)
    largest = limits.get("maxPerDoseMl")
    for line in lines:
        most = largest.get(line) if isinstance(largest, Mapping) else None
        if _positive(most) and most / ml_per_s[line] > DOSE_SECONDS[1]:
            raise ValueError(
                f"{where}: line {line}'s largest dose, {most:g} mL at {ml_per_s[line]:g} mL/s, "
                f"is {most / ml_per_s[line]:g} s of pumping; the rig runs a dose for at most "
                f"{DOSE_SECONDS[1]:g} s (limits.json maxPerDoseMl, hardware.json flowMlPerS)"
            )
    return RigZone(
        device_id=device_id,
        zone_id=zone_id,
        name=zone.get("name") or zone_id,
        gateway=gateway,
        rig_zone=rig_zone,
        pumps={line: pumps[line] for line in lines},
        pump_ml_per_s=ml_per_s,
        tds_ppm_per_ms_cm=float(scale),
        day_tank_level_sensors=sensors,
        limits=limits,
        dose=load_zone_dose(client_dir, zone_id),
    )


def _positive(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )
