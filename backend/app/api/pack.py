"""Reading the client's config pack (contract §2.5): the farm, which site this API serves, and what
the dose review needs to know about each zone.

Each reader takes the Pack that app.packs.load_pack gives (its folder in dev, its newest version in
the database on a server, T1-39), or a bare folder path."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..packcheck import pump_rates
from ..packs import Pack, PackError, as_pack
from .schemas import Drum, Farm, FarmZone, SensorCatalogue, ZoneCrop, ZoneProfile

log = logging.getLogger("api")

DIRECT_LINK = "MQTT"  # a controller on the farm's broker (§3); io_map.json names any other link


__all__ = ["Pack", "PackError"]  # PackError is still imported from here


@dataclass(frozen=True)
class SiteRef:
    """What every query is scoped by (OPEN-11)."""

    org_id: str
    site_id: str


def _without_meta(value: Any) -> Any:
    """The same data with every _meta block removed: they are never sent (§11.2)."""
    if isinstance(value, dict):
        return {key: _without_meta(entry) for key, entry in value.items() if key != "_meta"}
    if isinstance(value, list):
        return [_without_meta(entry) for entry in value]
    return value


def _cannot_load(reason: Exception) -> PackError:
    log.error("cannot load the config pack: %s", reason)
    return PackError("The farm layout could not be loaded")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


PackLike = Pack | Path


def read_farm(source: PackLike) -> Farm:
    """layout.json with one more field, features, the contents of features.json (§11.2), and
    every zone's source and the climate's filled in (§2.4)."""
    pack = as_pack(source)
    try:
        layout = pack.json("layout.json")
        features = pack.json("features.json")
        default = default_zone_source(features)
        zones = [
            {"source": default, **zone} if isinstance(zone, dict) else zone
            for zone in layout.get("zones", [])
        ]
        climate = layout.get("climate")
        if isinstance(climate, dict):
            climate = {"source": default, **climate}
        return Farm.model_validate(
            _without_meta({**layout, "zones": zones, "climate": climate, "features": features})
        )
    except (OSError, TypeError, ValueError, AttributeError) as exc:  # ValidationError: ValueError
        raise _cannot_load(exc) from exc


def default_zone_source(features: Any) -> str:
    """A zone layout.json gives no source is SIMULATED on a playground site and LIVE everywhere
    else (§2.4): a real farm's zone is never treated as simulated by mistake."""
    playground = isinstance(features, dict) and features.get("playground") is True
    return "SIMULATED" if playground else "LIVE"


def read_sensors(source: PackLike) -> SensorCatalogue | None:
    """sensors.json, the sensor catalogue per farm type (DM-3), or None for a pack without one."""
    pack = as_pack(source)
    try:
        return SensorCatalogue.model_validate(_without_meta(pack.json("sensors.json")))
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError, AttributeError) as exc:  # ValidationError: ValueError
        raise _cannot_load(exc) from exc


def zone_sources(source: PackLike) -> dict[str, str]:
    """Each zone's source, LIVE or SIMULATED, by zone ID."""
    return {zone.id: zone.source for zone in read_farm(source).zones}


def _profile(zone: FarmZone, links: dict[str, Any]) -> ZoneProfile:
    units = zone.growing_units or []
    link = (links.get(zone.controller) or {}).get("link") or DIRECT_LINK
    return ZoneProfile(
        name=zone.name,
        growing_system=zone.growing_system,
        growing_units=units,
        plant_sites=sum(unit.count * unit.plant_sites_each for unit in units) if units else None,
        size=zone.size,
        position=zone.position,
        tank=zone.reservoir,
        sensors=zone.sensors or {},
        controller=zone.controller,
        link=link,
        dose_lines=zone.dose_lines,
        notes=zone.notes,
    )


def _crop(recipes: dict[str, Any], zone_id: str) -> ZoneCrop | None:
    applied = (recipes.get("zones") or {}).get(zone_id)
    if applied is None:
        return None
    crop = next((c for c in recipes.get("crops", []) if c.get("id") == applied["crop"]), {})
    return ZoneCrop(
        crop=applied["crop"],
        name=crop.get("name"),
        variety=applied.get("variety"),
        stage=applied["stage"],
        planted_on=applied.get("plantedOn"),
    )


def read_zone_details(source: PackLike) -> dict[str, tuple[ZoneProfile, ZoneCrop | None]]:
    """Each zone's profile (layout.json, and its controller's link from io_map.json) and crop
    (recipes.json), keyed by zone ID (T1-38). A pack without io_map.json or recipes.json gives
    MQTT links and no crops."""
    pack = as_pack(source)
    try:
        farm = read_farm(pack)
        links = (pack.optional("io_map.json") or {}).get("controllers") or {}
        recipes = pack.optional("recipes.json") or {}
        return {zone.id: (_profile(zone, links), _crop(recipes, zone.id)) for zone in farm.zones}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise _cannot_load(exc) from exc


def site_of(source: PackLike) -> SiteRef:
    """The pack's organisation (ORG_ID, or the client's folder name until organisations exist,
    OPEN-11, as in the seed) and its layout.json site."""
    pack = as_pack(source)
    try:
        return SiteRef(pack.org_id, pack.json("layout.json")["site"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise _cannot_load(exc) from exc


@dataclass(frozen=True)
class Target:
    """The EC and pH a zone's recipe aims for (recipes.json)."""

    ec_min: float
    ec_max: float
    ph_min: float
    ph_max: float


@dataclass(frozen=True)
class LineStock:
    """What a dose line doses (nutrients.json doseLines.<line>, T1-38). None where the pack doesn't say."""

    name: str | None
    compound: str | None
    strength_pct: float | None
    drum: Drum | None
    purpose: str | None


def _stock(entry: dict[str, Any]) -> LineStock:
    drum = entry.get("drum")
    return LineStock(
        name=entry.get("name"),
        compound=entry.get("compound"),
        strength_pct=entry.get("strengthPct"),
        drum=Drum.model_validate(drum) if drum is not None else None,
        purpose=entry.get("purpose"),
    )


@dataclass(frozen=True)
class ZoneDosing:
    """Everything about dosing in one zone that comes from the config pack, never from code."""

    limits: dict[str, Any]  # limits.json zones.{zone}.limits, as written there
    mix_wait_s: float  # limits.json zones.{zone}.dose.mixWaitS
    target: Target | None  # None: no recipe applied to the zone
    # hardware.json flowMlPerS per minute: the zone's own pumps.zones.{zone}.doseLines.* where it
    # has them, else pumps.doseLines.* (packcheck.pump_rates)
    pump_ml_per_min: dict[str, float]
    day_tank_warn_pct: float  # alerts.json events.DAYTANK_LOW.thresholdPct
    ec_per_ml_per_l: dict[str, float]  # EC added per mL in each litre: nutrients.json
    ph_per_ml_per_l: dict[str, float]  # pH change per mL in each litre: the same
    # A SIMULATED zone on a playground site: its 24 h totals follow simulated time and start again
    # at a reset (§11.4). A LIVE zone's are on real time, always.
    simulated_time: bool
    stock: dict[str, LineStock]  # what each line doses: nutrients.json, T1-38
    # io_map.json controllers.{controller}.dayTankLevelSensors (DM-4): false where the drums have no
    # level sensor (client_B's rig), so the review can't read how much stock is left. True when the
    # pack has no io_map.json or the controller doesn't say.
    day_tank_sensors: bool = True


def _target(recipes: dict[str, Any], zone_id: str) -> Target | None:
    applied = recipes.get("zones", {}).get(zone_id)
    if applied is None:
        return None
    crop = next(c for c in recipes["crops"] if c["id"] == applied["crop"])
    stage = next(s for s in crop["stages"] if s["id"] == applied["stage"])
    ec, ph = stage["ecTarget"], stage["phTarget"]
    return Target(ec["min"], ec["max"], ph["min"], ph["max"])


def read_dosing(source: PackLike, zone_id: str) -> ZoneDosing | None:
    """The zone's dosing settings, or None if limits.json has no such zone."""
    pack = as_pack(source)
    try:
        zone = pack.json("limits.json")["zones"].get(zone_id)
        if zone is None:
            return None
        recipes = pack.json("recipes.json")
        pumps = pump_rates(pack.json("hardware.json"), zone_id)
        stock = pack.json("nutrients.json")["doseLines"]
        alerts = pack.json("alerts.json")
        features = pack.json("features.json")
        zone_source = zone_sources(pack).get(zone_id)
        return ZoneDosing(
            limits=zone["limits"],
            mix_wait_s=zone["dose"]["mixWaitS"],
            target=_target(recipes, zone_id),
            pump_ml_per_min={line: flow * 60 for line, (flow, _key) in pumps.items()},
            day_tank_warn_pct=alerts["events"]["DAYTANK_LOW"]["thresholdPct"],
            ec_per_ml_per_l={line: entry["ecChangePerMlPerL"] for line, entry in stock.items()},
            ph_per_ml_per_l={line: entry["phChangePerMlPerL"] for line, entry in stock.items()},
            simulated_time=features.get("playground") is True and zone_source == "SIMULATED",
            stock={line: _stock(entry) for line, entry in stock.items()},
            day_tank_sensors=_day_tank_sensors(pack, zone_id),
        )
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise _cannot_load(exc) from exc


def _day_tank_sensors(pack: Pack, zone_id: str) -> bool:
    """Whether the zone's controller can read its day-tanks' (drums') levels (DM-4)."""
    zone = next((z for z in pack.json("layout.json").get("zones", ()) if z.get("id") == zone_id), {})
    controllers = (pack.optional("io_map.json") or {}).get("controllers") or {}
    return (controllers.get(zone.get("controller")) or {}).get("dayTankLevelSensors") is not False


def run_settings(source: PackLike, zone_id: str, keys: tuple[str, ...]) -> dict[str, Any]:
    """The zone's limits.json dose settings named by keys (§2.5, §7.2)."""
    try:
        dose = as_pack(source).json("limits.json")["zones"][zone_id]["dose"]
        return {key: dose[key] for key in keys}
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise _cannot_load(exc) from exc


def pack_file(source: PackLike, name: str) -> Any:
    """Any pack file, parsed, such as scenarios.json or users.json."""
    try:
        return as_pack(source).json(name)
    except (OSError, TypeError, ValueError) as exc:
        raise _cannot_load(exc) from exc
