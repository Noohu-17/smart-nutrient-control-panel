"""Reading the client's config pack (contract §2.5): the farm, which site this API serves, and what
the dose review needs to know about each zone."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import Farm

log = logging.getLogger("api")


class PackError(Exception):
    """The config pack can't be read: the server's problem, so a 500 from app.errors."""


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


def read_farm(client_dir: Path) -> Farm:
    """layout.json with one more field, features, the contents of features.json (§11.2), and
    every zone's source filled in (§2.4)."""
    try:
        layout = _read_json(client_dir / "layout.json")
        features = _read_json(client_dir / "features.json")
        default = default_zone_source(features)
        zones = [
            {"source": default, **zone} if isinstance(zone, dict) else zone
            for zone in layout.get("zones", [])
        ]
        return Farm.model_validate(_without_meta({**layout, "zones": zones, "features": features}))
    except (OSError, TypeError, ValueError, AttributeError) as exc:  # ValidationError: ValueError
        raise _cannot_load(exc) from exc


def default_zone_source(features: Any) -> str:
    """A zone layout.json gives no source is SIMULATED on a playground site and LIVE everywhere
    else (§2.4): a real farm's zone is never treated as simulated by mistake."""
    playground = isinstance(features, dict) and features.get("playground") is True
    return "SIMULATED" if playground else "LIVE"


def zone_sources(client_dir: Path) -> dict[str, str]:
    """Each zone's source, LIVE or SIMULATED, by zone ID."""
    return {zone.id: zone.source for zone in read_farm(client_dir).zones}


def site_of(client_dir: Path) -> SiteRef:
    """org_id is the client's folder name until organisations exist (OPEN-11), as in the seed."""
    try:
        return SiteRef(client_dir.name, _read_json(client_dir / "layout.json")["site"])
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
class ZoneDosing:
    """Everything about dosing in one zone that comes from the config pack, never from code."""

    limits: dict[str, Any]  # limits.json zones.{zone}.limits, as written there
    mix_wait_s: float  # limits.json zones.{zone}.dose.mixWaitS
    target: Target | None  # None: no recipe applied to the zone
    pump_ml_per_min: dict[str, float]  # hardware.json pumps.doseLines.*.flowMlPerS, per minute
    day_tank_warn_pct: float  # alerts.json events.DAYTANK_LOW.thresholdPct
    ec_per_ml_per_l: dict[str, float]  # EC added per mL in each litre: nutrients.json
    ph_per_ml_per_l: dict[str, float]  # pH change per mL in each litre: the same
    # A SIMULATED zone on a playground site: its 24 h totals follow simulated time and start again
    # at a reset (§11.4). A LIVE zone's are on real time, always.
    simulated_time: bool


def _target(recipes: dict[str, Any], zone_id: str) -> Target | None:
    applied = recipes.get("zones", {}).get(zone_id)
    if applied is None:
        return None
    crop = next(c for c in recipes["crops"] if c["id"] == applied["crop"])
    stage = next(s for s in crop["stages"] if s["id"] == applied["stage"])
    ec, ph = stage["ecTarget"], stage["phTarget"]
    return Target(ec["min"], ec["max"], ph["min"], ph["max"])


def read_dosing(client_dir: Path, zone_id: str) -> ZoneDosing | None:
    """The zone's dosing settings, or None if limits.json has no such zone."""
    try:
        zone = _read_json(client_dir / "limits.json")["zones"].get(zone_id)
        if zone is None:
            return None
        recipes = _read_json(client_dir / "recipes.json")
        pumps = _read_json(client_dir / "hardware.json")["pumps"]["doseLines"]
        stock = _read_json(client_dir / "nutrients.json")["doseLines"]
        alerts = _read_json(client_dir / "alerts.json")
        features = _read_json(client_dir / "features.json")
        source = zone_sources(client_dir).get(zone_id)
        return ZoneDosing(
            limits=zone["limits"],
            mix_wait_s=zone["dose"]["mixWaitS"],
            target=_target(recipes, zone_id),
            pump_ml_per_min={line: pump["flowMlPerS"] * 60 for line, pump in pumps.items()},
            day_tank_warn_pct=alerts["events"]["DAYTANK_LOW"]["thresholdPct"],
            ec_per_ml_per_l={line: entry["ecChangePerMlPerL"] for line, entry in stock.items()},
            ph_per_ml_per_l={line: entry["phChangePerMlPerL"] for line, entry in stock.items()},
            simulated_time=features.get("playground") is True and source == "SIMULATED",
        )
    except (OSError, KeyError, TypeError, ValueError, StopIteration) as exc:
        raise _cannot_load(exc) from exc
