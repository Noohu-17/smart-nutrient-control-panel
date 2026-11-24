"""The config pack checks (contract 2.5): what "valid" means for a client's pack.

One set of checks for every way a pack arrives: scripts/validate_config.py runs them on the
folders in config/clients/, and the back end runs them on a pack before it is stored in the
database (app.db.config_store, T1-38, T1-39). Standard library only, so the script needs nothing
installed.

TODO: validate each file against its JSON Schema once the contract schemas
exist (docs/contract/schemas/, contract section 16). Until then, the checks
below are what "valid" means for a config pack.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
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
# T1-38: what a zone grows in. growingSystem is the zone's; each growingUnits entry is one kind of
# unit, with the size keys its type needs (all > 0) and its plant sites.
GROWING_SYSTEMS = {"NFT", "DWC", "DUTCH_BUCKET", "DRIP", "VERTICAL", "MIXED"}
UNIT_SIZE_KEYS = {
    "NFT_BENCH": ("channels", "lengthM"),  # a table or structure of NFT channels: a layout bench
    "DWC_RAFT": ("lengthM", "widthM"),
    "DUTCH_BUCKET": ("volumeL",),
    "GROW_BAG": ("volumeL",),
    "VERTICAL_TOWER": ("heightM",),
}
# The system each unit type belongs to (MIXED takes any).
UNIT_SYSTEM = {"NFT_BENCH": "NFT", "DWC_RAFT": "DWC", "DUTCH_BUCKET": "DUTCH_BUCKET",
               "GROW_BAG": "DRIP", "VERTICAL_TOWER": "VERTICAL"}
# The sensors a zone says it has fitted or not: every key, true or false (contract 6.1 readings).
SENSOR_KEYS = {"ec", "ph", "solTemp", "reservoirLevel", "circFlow", "dayTankLevel", "leak", "dissolvedOxygen"}
# What nutrients.json says about each dose line, besides its EC and pH change (T1-38).
DOSE_LINE_TEXT_KEYS = ("name", "compound", "purpose")
PLANTED_ON = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


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


def parse_json(text):
    """A pack file's text as JSON; a key written twice is an error, not the last one winning."""
    return json.loads(text, object_pairs_hook=_reject_duplicate_keys)


def load_json(path, errors):
    try:
        return parse_json(path.read_text(encoding="utf-8"))
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
        check_zone_details(where, zone, fail)

    climate = layout.get("climate") or {}
    if "source" in climate and climate["source"] not in ("LIVE", "SIMULATED"):
        fail(f"climate.source must be LIVE or SIMULATED, got {climate['source']!r}")
    if climate.get("source") == "SIMULATED" and not climate.get("airSensors"):
        fail("climate.airSensors: a SIMULATED climate needs at least one air sensor position")
    for zone_id, chiller in (climate.get("chillers") or {}).items():
        if zone_id not in dose_lines_by_zone:
            fail(f"climate.chillers: chiller {chiller} is keyed by {zone_id!r}, which is not a zone in this layout")

    for path, device_id in (("climate.controller", climate.get("controller")),
                            ("power.monitor", (layout.get("power") or {}).get("monitor"))):
        if device_id is not None and device_id not in device_by_id:
            fail(f"{path}: {device_id} is not listed in devices")

    return dose_lines_by_zone


def check_zone_details(where, zone, fail):
    """T1-38: the zone's growing system and units, size, position, tank, sensors and notes."""
    system = zone.get("growingSystem")
    if system not in GROWING_SYSTEMS:
        fail(f"{where}: growingSystem {system!r} must be one of {sorted(GROWING_SYSTEMS)}")

    units = zone.get("growingUnits")
    if not isinstance(units, list) or not units:
        fail(f'{where}: "growingUnits" must be a non-empty list')
        units = []
    nft_units = []
    for index, unit in enumerate(units):
        at = f"{where}: growingUnits[{index}]"
        if not isinstance(unit, dict):
            fail(f"{at} must be an object")
            continue
        kind = unit.get("type")
        if kind not in UNIT_SIZE_KEYS:
            fail(f"{at}: type {kind!r} must be one of {sorted(UNIT_SIZE_KEYS)}")
            continue
        if system in GROWING_SYSTEMS and system != "MIXED" and UNIT_SYSTEM[kind] != system:
            fail(f"{at}: a {kind} is not part of a {system} zone")
        for key in ("count", "plantSitesEach"):
            if not _positive_int(unit.get(key)):
                fail(f"{at}.{key} must be a whole number > 0")
        for key in UNIT_SIZE_KEYS[kind]:
            check = _positive_int if key == "channels" else _positive_number
            if not check(unit.get(key)):
                fail(f"{at}.{key} must be a {'whole ' if key == 'channels' else ''}number > 0")
        if kind == "NFT_BENCH":
            nft_units.append(unit)

    # NFT benches are the layout's benches too: the same number, and each one's size is a unit's.
    if nft_units:
        benches = [b for b in zone.get("benches") or [] if isinstance(b, dict)]
        counted = sum(u["count"] for u in nft_units if _positive_int(u.get("count")))
        if counted != len(benches):
            fail(f"{where}: growingUnits list {counted} NFT_BENCH units but benches lists {len(benches)}")
        sizes = {(u.get("channels"), u.get("lengthM")) for u in nft_units}
        for bench in benches:
            if (bench.get("channels"), bench.get("lengthM")) not in sizes:
                fail(f"{where}: bench {bench.get('id')} ({bench.get('channels')} channels, "
                     f"{bench.get('lengthM')} m) matches no NFT_BENCH unit")

    size = zone.get("size")
    if not isinstance(size, dict) or not all(_positive_number(size.get(k)) for k in ("lengthM", "widthM")):
        fail(f'{where}: "size" needs lengthM and widthM, numbers > 0')
    position = zone.get("position")
    if not isinstance(position, dict) or not all(_number(position.get(k)) for k in ("xM", "yM")):
        fail(f'{where}: "position" needs xM and yM, numbers (metres from the site\'s corner)')
    elif not isinstance(position.get("label", ""), str):
        fail(f"{where}: position.label must be text")
    if not isinstance(zone.get("notes"), str):
        fail(f'{where}: "notes" must be text ("" for none)')

    reservoir = zone.get("reservoir")
    reservoir = reservoir if isinstance(reservoir, dict) else {}
    volume, usable = reservoir.get("volumeL"), reservoir.get("usableL")
    if not _positive_number(volume) or not _positive_number(usable) or usable > volume:
        fail(f"{where}: reservoir needs volumeL and usableL, numbers > 0 with usableL no more than volumeL")

    sensors = zone.get("sensors")
    if not isinstance(sensors, dict) or set(sensors) != SENSOR_KEYS:
        got = sorted(sensors) if isinstance(sensors, dict) else sensors
        fail(f'{where}: "sensors" must say true or false for exactly {sorted(SENSOR_KEYS)}; got {got}')
    elif not all(isinstance(value, bool) for value in sensors.values()):
        fail(f"{where}: every sensors value must be true or false")


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
        if isinstance(zone, dict):
            check_limit_values(f"{rel}: zone {zone_id}", zone, errors)
        lines = dose_lines_by_zone.get(zone_id)
        limits = zone.get("limits") if isinstance(zone, dict) else None
        if lines is None or not isinstance(limits, dict):
            continue
        for key in LINE_KEYED_LIMITS:
            values = limits.get(key)
            if isinstance(values, dict) and set(values) != set(lines):
                errors.append(f"{rel}: zone {zone_id}: limits.{key} keys {sorted(values)} do not match doseLines {lines}")


def check_limit_values(where, zone, errors):
    """DM-9: a zone's safety limits must make sense, not only have the right keys. Per-dose and 24 h
    mL above 0, and per-dose no more than that line's 24 h; EC ceiling above 0; pH bounds within
    0-14 with min below max; reservoir % within 0-100; mix wait and dose duration not negative. A
    key the file leaves out is not checked here."""
    limits = zone.get("limits")
    if limits is not None and not isinstance(limits, dict):
        errors.append(f'{where}: "limits" must be an object')
        limits = None
    limits = limits or {}
    per_line = {}
    for key in LINE_KEYED_LIMITS:
        if key not in limits:
            continue
        values = limits[key]
        if not isinstance(values, dict):
            errors.append(f"{where}: limits.{key} must be an object keyed by dose line")
            continue
        for line, ml in values.items():
            if not _positive_number(ml):
                errors.append(f"{where}: limits.{key}.{line} must be mL > 0, got {ml!r}")
        per_line[key] = values
    per_dose, per_day = per_line.get("maxPerDoseMl", {}), per_line.get("max24hMl", {})
    for line, ml in per_dose.items():
        day = per_day.get(line)
        if _positive_number(ml) and _positive_number(day) and ml > day:
            errors.append(f"{where}: limits.maxPerDoseMl.{line} {ml} mL is more than max24hMl.{line} {day} mL")

    if "ecCeiling" in limits and not _positive_number(limits["ecCeiling"]):
        errors.append(f"{where}: limits.ecCeiling must be a number > 0, got {limits['ecCeiling']!r}")
    ph = {}
    for key in ("phMin", "phMax"):
        if key not in limits:
            continue
        value = limits[key]
        if _number(value) and 0 <= value <= 14:
            ph[key] = value
        else:
            errors.append(f"{where}: limits.{key} must be a number within 0-14, got {value!r}")
    if len(ph) == 2 and ph["phMin"] >= ph["phMax"]:
        errors.append(f"{where}: limits.phMin {ph['phMin']} must be below phMax {ph['phMax']}")
    if "minReservoirPctToDose" in limits:
        value = limits["minReservoirPctToDose"]
        if not (_number(value) and 0 <= value <= 100):
            errors.append(f"{where}: limits.minReservoirPctToDose must be a number within 0-100, got {value!r}")

    dose = zone.get("dose")
    if dose is not None and not isinstance(dose, dict):
        errors.append(f'{where}: "dose" must be an object')
        return
    for key in ("mixWaitS", "maxDurationS"):
        if key in (dose or {}) and not (_number(dose[key]) and dose[key] >= 0):
            errors.append(f"{where}: dose.{key} must be a number of seconds, not negative, got {dose[key]!r}")


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
        if not isinstance(zone, dict):
            continue
        if not _text(zone.get("variety")):
            errors.append(f"{rel}: zone {zone_id}: variety must be the crop's variety, as text")
        if not _date(zone.get("plantedOn")):
            errors.append(f"{rel}: zone {zone_id}: plantedOn must be the day it was planted, YYYY-MM-DD; "
                          f"got {zone.get('plantedOn')!r}")


def check_nutrients(rel, nutrients, dose_lines_by_zone, errors, layout=None):
    """Every dose line in the layout needs its stock's EC and pH change per mL in each litre, and
    what the stock is (T1-38): name, compound, strengthPct, drum and purpose."""
    lines = nutrients.get("doseLines")
    if not isinstance(lines, dict):
        errors.append(f'{rel}: "doseLines" must be an object keyed by dose line')
        return
    used = sorted({line for zone_lines in dose_lines_by_zone.values() for line in zone_lines})
    missing = [line for line in used if line not in lines]
    if missing:
        errors.append(f"{rel}: no entry for dose lines {missing} used in the layout")
    day_tank_l = _day_tank_litres(layout)
    drum_lines = {}
    for line, entry in lines.items():
        entry = entry if isinstance(entry, dict) else {}
        where = f"{rel}: doseLines.{line}"
        for key in ("ecChangePerMlPerL", "phChangePerMlPerL"):
            if not _number(entry.get(key)):
                errors.append(f"{where}.{key} must be a number")
        for key in DOSE_LINE_TEXT_KEYS:
            if not _text(entry.get(key)):
                errors.append(f"{where}.{key} must be text")
        strength = entry.get("strengthPct")
        if not _positive_number(strength) or strength > 100:
            errors.append(f"{where}.strengthPct must be a number > 0 and no more than 100")
        drum = entry.get("drum")
        drum = drum if isinstance(drum, dict) else {}
        if not _text(drum.get("id")) or not _positive_number(drum.get("volumeL")):
            errors.append(f"{where}.drum needs an id (text) and volumeL (a number > 0)")
            continue
        if drum["id"] in drum_lines:
            errors.append(f"{where}.drum: {drum['id']} already holds line {drum_lines[drum['id']]}'s stock")
        drum_lines[drum["id"]] = line
        largest = max(day_tank_l.get(line, [0]))
        if largest > drum["volumeL"]:
            errors.append(f"{where}.drum: {drum['volumeL']:g} L is less than a {largest:g} L day-tank "
                          "it fills (layout.json dayTanks)")


def pump_rates(hardware, zone_id):
    """Each dose line's pump rate in one zone (hardware.json, contract 2.5), as
    {line: (flowMlPerS as written, the key it is under)}. A zone whose pumps run at their own rate
    has pumps.zones.<zone>.doseLines.<line>; every other line of every zone takes
    pumps.doseLines.<line>, the rate the zones share."""
    pumps = hardware.get("pumps") if isinstance(hardware, dict) else None
    pumps = pumps if isinstance(pumps, dict) else {}
    zones = pumps.get("zones") if isinstance(pumps.get("zones"), dict) else {}
    own = zones.get(zone_id) if isinstance(zones.get(zone_id), dict) else {}
    rates = {}
    for key, lines in (("pumps.doseLines", pumps.get("doseLines")),
                       (f"pumps.zones.{zone_id}.doseLines", own.get("doseLines"))):
        for line, entry in (lines.items() if isinstance(lines, dict) else ()):
            flow = entry.get("flowMlPerS") if isinstance(entry, dict) else None
            rates[line] = (flow, f"{key}.{line}.flowMlPerS")
    return rates


def pump_rate(hardware, zone_id, line):
    """One line's (flowMlPerS as written, its key) in one zone; (None, the shared key) if unset."""
    return pump_rates(hardware, zone_id).get(line, (None, f"pumps.doseLines.{line}.flowMlPerS"))


def check_pump_rates(rel, hardware, dose_lines_by_zone, errors):
    """Each dose line's pump rate in each zone (pump_rates), which the dose review's estimate and
    the dose-line answer use (T1-38). pumps.zones is keyed by layout zones and their dose lines."""
    pumps = hardware.get("pumps") if isinstance(hardware, dict) else None
    zones = pumps.get("zones") if isinstance(pumps, dict) else None
    if zones is not None and not isinstance(zones, dict):
        errors.append(f"{rel}: pumps.zones must be an object keyed by layout.json zone IDs")
        zones = {}
    for zone_id, entry in (zones or {}).items():
        where = f"{rel}: pumps.zones.{zone_id}"
        if zone_id not in dose_lines_by_zone:
            errors.append(f"{where}: layout.json has no zone {zone_id}")
        elif not isinstance(entry, dict) or not isinstance(entry.get("doseLines"), dict):
            errors.append(f'{where}: needs a "doseLines" object keyed by the zone\'s dose lines')
        else:
            for line in sorted(set(entry["doseLines"]) - set(dose_lines_by_zone[zone_id])):
                errors.append(f"{where}.doseLines.{line}: zone {zone_id} has no dose line {line} "
                              f"(its doseLines are {dose_lines_by_zone[zone_id]})")
    failed = set()
    for zone_id, lines in dose_lines_by_zone.items():
        for line in lines:
            flow, key = pump_rate(hardware, zone_id, line)
            if not _positive_number(flow) and key not in failed:
                failed.add(key)
                errors.append(f"{rel}: {key} must be a number > 0 "
                              "(every dose line in the layout needs its pump rate)")


def _day_tank_litres(layout):
    """{line: [usableL of each zone's day-tank]} from layout.json."""
    litres = {}
    for zone in (layout or {}).get("zones", []) if isinstance(layout, dict) else []:
        for line, tank in ((zone.get("dayTanks") or {}) if isinstance(zone, dict) else {}).items():
            if isinstance(tank, dict) and _positive_number(tank.get("usableL")):
                litres.setdefault(line, []).append(tank["usableL"])
    return litres


def validate_client(client_dir, errors):
    """Validate one client folder. Returns the number of JSON files read."""
    files = sorted(client_dir.glob("*.json"))
    loaded = {}
    for path in files:
        data = load_json(path, errors)
        if data is not None:
            loaded[path.name] = data
    check_pack(client_dir.name, lambda name: display(client_dir / name), {p.name for p in files}, loaded, errors)
    return len(files)


def validate_texts(client, texts):
    """Validate a pack given as {file name: its text}, as the database stores it (T1-39), with the
    same checks as a folder. Returns the problems, one line each; none means valid."""
    errors = []
    loaded = {}
    for name, text in sorted(texts.items()):
        try:
            loaded[name] = parse_json(text)
        except ValueError as exc:
            errors.append(f"{client}/{name}: not valid JSON: {exc}")
    check_pack(client, lambda name: f"{client}/{name}", set(texts), loaded, errors)
    return errors


def check_pack(client, where, names, loaded, errors):
    """Every check on one pack. names: the files it has; loaded: the ones that parsed, by name;
    where(name): how a problem names the file."""
    for name in PACK_FILES:
        if name not in names:
            errors.append(f"{where('').rstrip('/')}: missing {name} (contract 2.5)")

    dose_lines_by_zone = None
    layout = loaded.get("layout.json")
    if isinstance(layout, dict):
        dose_lines_by_zone = check_layout(client, where("layout.json"), layout, errors)
    elif layout is not None:
        errors.append(f"{where('layout.json')}: top level must be a JSON object")

    for name, data in loaded.items():
        rel = where(name)
        if name not in REAL_VALUE_FILES:
            check_placeholder_meta(rel, data, errors)
        if name in ZONE_KEYED_FILES and dose_lines_by_zone and isinstance(data, dict):
            check_zone_keys(rel, name, data, dose_lines_by_zone, errors)
        if name == "nutrients.json" and dose_lines_by_zone and isinstance(data, dict):
            check_nutrients(rel, data, dose_lines_by_zone, errors, layout)
        if name == "hardware.json" and dose_lines_by_zone:
            check_pump_rates(rel, data, dose_lines_by_zone, errors)

    io_map = loaded.get("io_map.json")
    if isinstance(io_map, dict) and isinstance(layout, dict):
        check_rig_controllers(where("io_map.json"), io_map, layout,
                              loaded.get("hardware.json"), errors, loaded.get("limits.json"),
                              loaded.get("features.json"))


RIG_RELAYS = ("relay1", "relay2", "relay3")  # the rig's dosing pumps (relay4 is its inlet)
RIG_MAX_DOSE_S = 600  # the rig runs one timed dose for at most this long (sf/cmd/1)


def check_rig_controllers(rel, io_map, layout, hardware, errors, limits=None, features=None):
    """A controller run by the zone controller service on the prototype rig (T1-32,
    simulator/sim/rig): `"link": "SF_GATEWAY"` in io_map.json controllers. A line's largest
    dose (limits.json maxPerDoseMl) must fit in one rig dose at its pump's flow in that zone
    (pump_rates), and its zone is LIVE (contract 2.4)."""
    playground = isinstance(features, dict) and features.get("playground") is True
    default_source = "SIMULATED" if playground else "LIVE"
    controllers = io_map.get("controllers")
    if not isinstance(controllers, dict):
        errors.append(f"{rel}: controllers must be an object")
        return
    zones = {z.get("controller"): z for z in layout.get("zones", []) if isinstance(z, dict)}
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
            flow, key = pump_rate(hardware, zone.get("id"), line)
            if not _positive_number(flow):
                errors.append(f"{where}: hardware.json {key} must be a number > 0 "
                              "(a dose runs mL / flow seconds)")
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


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _positive_number(value):
    return _number(value) and value > 0


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _date(value):
    """A calendar date as YYYY-MM-DD that exists (2026-02-30 doesn't)."""
    if not isinstance(value, str) or not PLANTED_ON.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


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
