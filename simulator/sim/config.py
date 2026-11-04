"""Read the client's config pack (contract 2.5): the same files the back end loads."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SITE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # contract 2.1: lowercase slug
DEVICE_ID = re.compile(r"[A-Z][A-Z0-9]*")  # contract 2.2: GW1, ZC1 ...; also the MQTT client ID
# The kinds of device the simulator knows how to play (layout.json "devices").
DEVICE_KINDS = ("GATEWAY", "ZONE_CONTROLLER", "CLIMATE_CONTROLLER", "POWER_MONITOR")


def read_pack_file(client_dir: Path, name: str) -> dict[str, Any]:
    path = client_dir / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"config pack file not found: {path}") from None
    except json.JSONDecodeError as err:
        raise ValueError(f"{path}: not valid JSON: {err}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    return data


def load_speed_options(client_dir: Path) -> tuple[float, ...]:
    """Clock speeds the playground offers (simulator.json "speedOptions", contract 11.4)."""
    options = read_pack_file(client_dir, "simulator.json").get("speedOptions")
    if not isinstance(options, list) or not options or not all(
        isinstance(x, (int, float)) and not isinstance(x, bool) and x >= 0 for x in options
    ):
        raise ValueError(f"simulator.json: speedOptions must list speeds >= 0, got {options!r}")
    return tuple(options)


def playground_enabled(client_dir: Path) -> bool:
    """features.json "playground": only playground sites carry simTs (contract 5)."""
    value = read_pack_file(client_dir, "features.json").get("playground", False)
    if not isinstance(value, bool):
        raise ValueError(f"features.json: playground must be true or false, got {value!r}")
    return value


def load_zone_limits(client_dir: Path, zone_id: str) -> dict[str, Any]:
    """A zone controller's own limits (limits.json), the shape DEVICE_CONFIG brings (7.7)."""
    zones = read_pack_file(client_dir, "limits.json").get("zones")
    zone = zones.get(zone_id) if isinstance(zones, dict) else None
    limits = zone.get("limits") if isinstance(zone, dict) else None
    if not isinstance(limits, dict):
        raise ValueError(f"limits.json: zones.{zone_id}.limits must be an object, got {limits!r}")
    return limits


def load_site(client_dir: Path) -> str:
    """The site ID used in every topic, farm/{site}/... (layout.json "site")."""
    site = read_pack_file(client_dir, "layout.json").get("site")
    if not isinstance(site, str) or not SITE_ID.fullmatch(site):
        raise ValueError(f"layout.json: site must be a lowercase slug, got {site!r}")
    return site


def load_devices(client_dir: Path) -> tuple[dict[str, Any], ...]:
    """The devices to play: one per entry in layout.json "devices", in file order.

    Each entry has an "id" and a "kind", plus kind-specific fields such as a zone
    controller's "zone". Remove an entry and that device is no longer played.
    """
    devices = read_pack_file(client_dir, "layout.json").get("devices")
    if not isinstance(devices, list) or not devices:
        raise ValueError(f'layout.json: "devices" must be a non-empty list, got {devices!r}')
    seen = set()
    for device in devices:
        if not isinstance(device, dict):
            raise ValueError(f"layout.json: each device must be an object, got {device!r}")
        device_id, kind = device.get("id"), device.get("kind")
        if not isinstance(device_id, str) or not DEVICE_ID.fullmatch(device_id):
            raise ValueError(f"layout.json: device id must look like GW1 or ZC1, got {device_id!r}")
        if device_id in seen:
            raise ValueError(f"layout.json: device {device_id} is listed more than once")
        if kind not in DEVICE_KINDS:
            raise ValueError(
                f"layout.json: device {device_id} kind must be one of {list(DEVICE_KINDS)}, "
                f"got {kind!r}"
            )
        seen.add(device_id)
    return tuple(devices)
