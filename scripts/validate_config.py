#!/usr/bin/env python3
"""Validate the client configuration packs in config/clients/.

Parses every config/clients/*/*.json and checks layout.json against
docs/contract.md section 2. Standard library only (Python 3).

Usage:
    python scripts/validate_config.py [CLIENTS_DIR]

CLIENTS_DIR defaults to config/clients/ in this repo. Exits 0 when every
check passes and 1 on any failure, printing one line per problem.

TODO: validate each file against its JSON Schema once the contract schemas
exist (docs/contract/schemas/, contract section 16). Until then, the checks
below are what "valid" means for a config pack.
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLIENTS_DIR = REPO_ROOT / "config" / "clients"

# Contract 2.5: every client pack has these files.
PACK_FILES = [
    "layout.json", "limits.json", "recipes.json", "alerts.json", "climate.json",
    "features.json", "users.json", "simulator.json", "hardware.json", "nutrients.json",
    "io_map.json", "scenarios.json",
]

# These hold real values. Every other file must carry _meta.placeholder.
REAL_VALUE_FILES = {"layout.json", "features.json"}

# Files whose "zones" object must be keyed by exactly the layout's zone IDs.
ZONE_KEYED_FILES = {"limits.json", "alerts.json", "recipes.json"}

# Limits keyed by dose line; their keys must match the zone's doseLines.
LINE_KEYED_LIMITS = ["maxPerDoseMl", "max24hMl"]

# Contract 2.2: the devices each client's layout must list, exactly.
EXPECTED_DEVICES = {
    "client_A": {"GW1", "ZC1", "ZC2", "CC1", "PM1"},
}

ZONE_ID = re.compile(r"Z[1-9][0-9]*")

# Contract 2.4: real hardware runs the zone, or the simulator. Left out, a zone is SIMULATED on a
# playground site and LIVE everywhere else.
ZONE_SOURCES = ("LIVE", "SIMULATED")


def _reject_duplicate_keys(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate key {key!r}")
        obj[key] = value
    return obj


def display(path):
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def load_json(path, errors):
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, ValueError) as exc:
        errors.append(f"{display(path)}: not valid JSON: {exc}")
        return None


def check_layout(client, rel, layout, errors):
    """Check layout.json. Returns {zoneId: doseLines} for the cross-file checks."""
    def fail(msg):
        errors.append(f"{rel}: {msg}")

    if layout.get("v") != 1:
        fail(f'"v" must be 1, got {layout.get("v")!r}')

    devices = layout.get("devices")
    if not isinstance(devices, list):
        fail('"devices" must be a list of {"id", "kind"} objects')
        devices = []
    device_by_id = {}
    for device in devices:
        device_id = device.get("id") if isinstance(device, dict) else None
        if not isinstance(device_id, str):
            fail(f"device entry without a string id: {device!r}")
        elif device_id in device_by_id:
            fail(f"device {device_id} is listed more than once")
        else:
            device_by_id[device_id] = device

    expected = EXPECTED_DEVICES.get(client)
    if expected is not None and set(device_by_id) != expected:
        missing = sorted(expected - set(device_by_id))
        extra = sorted(set(device_by_id) - expected)
        fail(f"devices must be exactly {sorted(expected)}; missing {missing}, unexpected {extra}")

    zones = layout.get("zones")
    if not isinstance(zones, list) or not zones:
        fail('"zones" must be a non-empty list')
        return {}

    dose_lines_by_zone = {}
    bench_ids = set()
    for index, zone in enumerate(zones):
        zone_id = zone.get("id") if isinstance(zone, dict) else None
        where = f"zones[{index}]"
        if not isinstance(zone_id, str) or not ZONE_ID.fullmatch(zone_id):
            fail(f"{where}: zone id {zone_id!r} does not match Z{{n}}")
            continue
        if zone_id in dose_lines_by_zone:
            fail(f"zone id {zone_id} is used more than once")
            continue
        where = f"zone {zone_id}"

        if "source" in zone and zone["source"] not in ZONE_SOURCES:
            fail(f"{where}: source must be one of {list(ZONE_SOURCES)}, got {zone['source']!r}")

        bench_pattern = re.compile(re.escape(zone_id) + r"-B[1-9][0-9]*")
        for bench in zone.get("benches") or []:
            bench_id = bench.get("id") if isinstance(bench, dict) else None
            if not isinstance(bench_id, str) or not bench_pattern.fullmatch(bench_id):
                fail(f"{where}: bench id {bench_id!r} does not match {zone_id}-B{{n}}")
            elif bench_id in bench_ids:
                fail(f"{where}: bench id {bench_id} is used more than once")
            else:
                bench_ids.add(bench_id)

        controller = zone.get("controller")
        device = device_by_id.get(controller)
        if device is None:
            fail(f"{where}: controller {controller!r} is not listed in devices")
        elif device.get("kind") != "ZONE_CONTROLLER" or device.get("zone") != zone_id:
            fail(f'{where}: device {controller} must have kind "ZONE_CONTROLLER" and zone "{zone_id}"')

        dose_lines = zone.get("doseLines")
        day_tanks = zone.get("dayTanks")
        if not isinstance(dose_lines, list) or not isinstance(day_tanks, dict):
            fail(f'{where}: needs a "doseLines" list and a "dayTanks" object')
            dose_lines = []
        elif len(set(dose_lines)) != len(dose_lines) or set(dose_lines) != set(day_tanks):
            fail(f"{where}: doseLines {dose_lines} do not match dayTanks keys {sorted(day_tanks)}")
        dose_lines_by_zone[zone_id] = dose_lines

    climate = layout.get("climate") or {}
    for zone_id, chiller in (climate.get("chillers") or {}).items():
        if zone_id not in dose_lines_by_zone:
            fail(f"climate.chillers: chiller {chiller} is keyed by {zone_id!r}, which is not a zone in this layout")

    for path, device_id in (("climate.controller", climate.get("controller")),
                            ("power.monitor", (layout.get("power") or {}).get("monitor"))):
        if device_id is not None and device_id not in device_by_id:
            fail(f"{path}: {device_id} is not listed in devices")

    return dose_lines_by_zone


def check_placeholder_meta(rel, data, errors):
    if not isinstance(data, dict):
        errors.append(f"{rel}: top level must be a JSON object")
        return
    if data.get("v") != 1:
        errors.append(f'{rel}: "v" must be 1, got {data.get("v")!r}')
    meta = data.get("_meta")
    if not isinstance(meta, dict) or not isinstance(meta.get("placeholder"), bool):
        errors.append(f'{rel}: needs "_meta.placeholder" (true or false); only layout.json and features.json may leave it out')


def check_zone_keys(rel, name, data, dose_lines_by_zone, errors):
    zones = data.get("zones")
    if not isinstance(zones, dict):
        errors.append(f'{rel}: "zones" must be an object keyed by zone ID')
        return
    if set(zones) != set(dose_lines_by_zone):
        errors.append(f"{rel}: zones {sorted(zones)} do not match the layout's zone IDs {sorted(dose_lines_by_zone)}")
    if name == "recipes.json":
        check_zone_recipes(rel, data, zones, errors)
    if name != "limits.json":
        return
    for zone_id, zone in zones.items():
        lines = dose_lines_by_zone.get(zone_id)
        limits = zone.get("limits") if isinstance(zone, dict) else None
        if lines is None or not isinstance(limits, dict):
            continue
        for key in LINE_KEYED_LIMITS:
            values = limits.get(key)
            if isinstance(values, dict) and set(values) != set(lines):
                errors.append(f"{rel}: zone {zone_id}: limits.{key} keys {sorted(values)} do not match doseLines {lines}")


def check_zone_recipes(rel, recipes, zones, errors):
    """Each zone's crop and stage must be one of the recipes' own."""
    stages = {
        crop.get("id"): {stage.get("id") for stage in crop.get("stages", []) if isinstance(stage, dict)}
        for crop in recipes.get("crops", [])
        if isinstance(crop, dict)
    }
    for zone_id, zone in zones.items():
        crop = zone.get("crop") if isinstance(zone, dict) else None
        stage = zone.get("stage") if isinstance(zone, dict) else None
        if crop not in stages or stage not in stages[crop]:
            errors.append(f"{rel}: zone {zone_id}: no recipe for crop {crop!r} at stage {stage!r}")


def check_nutrients(rel, nutrients, dose_lines_by_zone, errors):
    """Every dose line in the layout needs its stock's EC and pH change per mL in each litre."""
    lines = nutrients.get("doseLines")
    if not isinstance(lines, dict):
        errors.append(f'{rel}: "doseLines" must be an object keyed by dose line')
        return
    used = sorted({line for zone_lines in dose_lines_by_zone.values() for line in zone_lines})
    missing = [line for line in used if line not in lines]
    if missing:
        errors.append(f"{rel}: no entry for dose lines {missing} used in the layout")
    for line, entry in lines.items():
        for key in ("ecChangePerMlPerL", "phChangePerMlPerL"):
            value = entry.get(key) if isinstance(entry, dict) else None
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                errors.append(f"{rel}: doseLines.{line}.{key} must be a number")


def validate_client(client_dir, errors):
    """Validate one client folder. Returns the number of JSON files read."""
    client = client_dir.name
    for name in PACK_FILES:
        if not (client_dir / name).is_file():
            errors.append(f"{display(client_dir)}: missing {name} (contract 2.5)")

    files = sorted(client_dir.glob("*.json"))
    loaded = {}
    for path in files:
        data = load_json(path, errors)
        if data is not None:
            loaded[path.name] = data

    dose_lines_by_zone = None
    layout = loaded.get("layout.json")
    if isinstance(layout, dict):
        dose_lines_by_zone = check_layout(client, display(client_dir / "layout.json"), layout, errors)
    elif layout is not None:
        errors.append(f"{display(client_dir / 'layout.json')}: top level must be a JSON object")

    for name, data in loaded.items():
        rel = display(client_dir / name)
        if name not in REAL_VALUE_FILES:
            check_placeholder_meta(rel, data, errors)
        if name in ZONE_KEYED_FILES and dose_lines_by_zone and isinstance(data, dict):
            check_zone_keys(rel, name, data, dose_lines_by_zone, errors)
        if name == "nutrients.json" and dose_lines_by_zone and isinstance(data, dict):
            check_nutrients(rel, data, dose_lines_by_zone, errors)

    io_map = loaded.get("io_map.json")
    if isinstance(io_map, dict) and isinstance(layout, dict):
        check_rig_controllers(display(client_dir / "io_map.json"), io_map, layout,
                              loaded.get("hardware.json"), errors, loaded.get("limits.json"),
                              loaded.get("features.json"))

    return len(files)


RIG_RELAYS = ("relay1", "relay2", "relay3")  # the rig's dosing pumps (relay4 is its inlet)
RIG_MAX_DOSE_S = 600  # the rig runs one timed dose for at most this long (sf/cmd/1)


def check_rig_controllers(rel, io_map, layout, hardware, errors, limits=None, features=None):
    """A controller run by the zone controller service on the prototype rig (T1-32,
    simulator/sim/rig): `"link": "SF_GATEWAY"` in io_map.json controllers. A line's largest
    dose (limits.json maxPerDoseMl) must fit in one rig dose at its pump's flow, and its zone is
    LIVE (contract 2.4)."""
    playground = isinstance(features, dict) and features.get("playground") is True
    default_source = "SIMULATED" if playground else "LIVE"
    controllers = io_map.get("controllers")
    if not isinstance(controllers, dict):
        errors.append(f"{rel}: controllers must be an object")
        return
    zones = {z.get("controller"): z for z in layout.get("zones", []) if isinstance(z, dict)}
    flows = ((hardware or {}).get("pumps") or {}).get("doseLines") or {}
    for device, entry in controllers.items():
        if not isinstance(entry, dict) or entry.get("link") != "SF_GATEWAY":
            continue
        where = f"{rel}: controllers.{device}"
        zone = zones.get(device)
        if zone is None:
            errors.append(f"{where}: no layout.json zone has controller {device}")
            continue
        if zone.get("source", default_source) != "LIVE":
            errors.append(f"{where}: zone {zone.get('id')} runs on the rig, so its layout.json "
                          "source must be LIVE")
        if not isinstance(entry.get("gateway"), str) or not entry["gateway"]:
            errors.append(f"{where}.gateway must be the gateway's client ID")
        if entry.get("rigZone") not in (1, 2) or isinstance(entry.get("rigZone"), bool):
            errors.append(f"{where}.rigZone must be 1 or 2")
        lines, pumps = list(zone.get("doseLines") or []), entry.get("pumps")
        if not isinstance(pumps, dict) or sorted(pumps) != sorted(lines):
            errors.append(f"{where}.pumps must map each dose line {lines} to a relay")
        elif (any(r not in RIG_RELAYS for r in pumps.values())
              or len(set(pumps.values())) != len(pumps)):
            errors.append(f"{where}.pumps: each line needs its own relay of {list(RIG_RELAYS)}")
        largest = _max_per_dose(limits, zone.get("id"))
        for line in lines:
            flow = (flows.get(line) or {}).get("flowMlPerS")
            if not _positive_number(flow):
                errors.append(f"{where}: hardware.json pumps.doseLines.{line}.flowMlPerS "
                              "must be a number > 0 (a dose runs mL / flow seconds)")
                continue
            most = largest.get(line) if isinstance(largest, dict) else None
            if _positive_number(most) and most / flow > RIG_MAX_DOSE_S:
                errors.append(f"{where}: line {line}'s largest dose, {most:g} mL at {flow:g} "
                              f"mL/s, is {most / flow:g} s of pumping; the rig runs a dose for "
                              f"at most {RIG_MAX_DOSE_S} s (limits.json maxPerDoseMl)")
        if not _positive_number(entry.get("tdsPpmPerMsCm")):
            errors.append(f"{where}.tdsPpmPerMsCm must be a number > 0")
        if not isinstance(entry.get("dayTankLevelSensors"), bool):
            errors.append(f"{where}.dayTankLevelSensors must be true or false")


def _max_per_dose(limits, zone_id):
    """limits.json zones.<zone>.limits.maxPerDoseMl, or None if the file doesn't say."""
    node = limits
    for key in ("zones", zone_id, "limits", "maxPerDoseMl"):
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else None


def _positive_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def main(argv):
    clients_dir = Path(argv[1]) if len(argv) > 1 else DEFAULT_CLIENTS_DIR
    if not clients_dir.is_dir():
        print(f"FAIL {clients_dir}: not a directory", file=sys.stderr)
        return 1
    client_dirs = sorted(p for p in clients_dir.iterdir() if p.is_dir())
    if not client_dirs:
        print(f"FAIL {display(clients_dir)}: no client folders found", file=sys.stderr)
        return 1

    errors = []
    for client_dir in client_dirs:
        before = len(errors)
        count = validate_client(client_dir, errors)
        status = "OK" if len(errors) == before else f"{len(errors) - before} problem(s)"
        print(f"{display(client_dir)}: {count} files, {status}")

    if errors:
        sys.stdout.flush()
        print(file=sys.stderr)
        for error in errors:
            print(f"FAIL {error}", file=sys.stderr)
        print(f"\n{len(errors)} problem(s) found.", file=sys.stderr)
        return 1
    print(f"All {len(client_dirs)} client pack(s) valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
