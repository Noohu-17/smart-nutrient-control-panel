"""scripts/validate_config.py's checks of what a zone, crop and dose line are (T1-38).

Each test breaks one thing in a copy of client_B's pack and runs the script on it, as a person
would: it must fail, and say what is wrong.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "validate_config.py"
CLIENTS = REPO / "config" / "clients"

if not SCRIPT.exists():
    pytest.skip("no scripts/ here (the Docker test image has none)", allow_module_level=True)


def run(clients: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(clients)], capture_output=True, text=True, check=False
    )


def broken(tmp_path: Path, name: str, change) -> str:
    """client_B with `change(data)` applied to its `name` file; returns what the script said."""
    clients = tmp_path / "clients"
    pack = shutil.copytree(CLIENTS / "client_B", clients / "client_B")
    data = json.loads((pack / name).read_text(encoding="utf-8"))
    change(data)
    (pack / name).write_text(json.dumps(data), encoding="utf-8")
    result = run(clients)
    assert result.returncode == 1, result.stdout
    return result.stderr


def zone(data, index=0):
    return data["zones"][index]


def test_both_client_packs_are_valid():
    result = run(CLIENTS)
    assert result.returncode == 0, result.stderr
    assert "All 2 client pack(s) valid." in result.stdout


@pytest.mark.parametrize(
    ("name", "change", "said"),
    [
        ("layout.json", lambda d: zone(d).pop("growingSystem"), "growingSystem None must be one of"),
        ("layout.json", lambda d: zone(d)["growingUnits"][0].update(type="RAFT"), "type 'RAFT' must be one of"),
        ("layout.json", lambda d: zone(d)["growingUnits"][0].update(type="GROW_BAG", volumeL=10),
         "a GROW_BAG is not part of a NFT zone"),
        ("layout.json", lambda d: zone(d)["growingUnits"][0].update(plantSitesEach=0), "plantSitesEach must be a whole number > 0"),
        ("layout.json", lambda d: zone(d)["growingUnits"][0].update(count=3), "list 3 NFT_BENCH units but benches lists 4"),
        ("layout.json", lambda d: zone(d)["growingUnits"][0].update(lengthM=2.4), "matches no NFT_BENCH unit"),
        ("layout.json", lambda d: zone(d).update(size={"lengthM": 3}), '"size" needs lengthM and widthM'),
        ("layout.json", lambda d: zone(d).pop("position"), '"position" needs xM and yM'),
        ("layout.json", lambda d: zone(d).pop("notes"), '"notes" must be text'),
        ("layout.json", lambda d: zone(d)["reservoir"].update(volumeL=8), "usableL no more than volumeL"),
        ("layout.json", lambda d: zone(d)["sensors"].pop("leak"), '"sensors" must say true or false for exactly'),
        ("layout.json", lambda d: zone(d)["sensors"].update(ph="yes"), "every sensors value must be true or false"),
        ("recipes.json", lambda d: d["zones"]["Z1"].pop("variety"), "zone Z1: variety must be"),
        ("recipes.json", lambda d: d["zones"]["Z2"].update(plantedOn="2026-02-30"), "zone Z2: plantedOn must be"),
        ("recipes.json", lambda d: d["zones"]["Z1"].update(plantedOn="10/11/2026"), "zone Z1: plantedOn must be"),
        ("nutrients.json", lambda d: d["doseLines"]["N"].pop("compound"), "doseLines.N.compound must be text"),
        ("nutrients.json", lambda d: d["doseLines"]["P"].update(strengthPct=0), "doseLines.P.strengthPct must be a number > 0"),
        ("nutrients.json", lambda d: d["doseLines"]["K"].update(strengthPct=140), "doseLines.K.strengthPct must be a number > 0"),
        ("nutrients.json", lambda d: d["doseLines"]["N"].pop("drum"), "doseLines.N.drum needs an id"),
        ("nutrients.json", lambda d: d["doseLines"]["N"]["drum"].update(volumeL=5),
         "doseLines.N.drum: 5 L is less than a 10 L day-tank"),
        ("nutrients.json", lambda d: d["doseLines"]["P"]["drum"].update(id="DRUM-N"), "DRUM-N already holds line N's stock"),
        ("hardware.json", lambda d: d["pumps"]["doseLines"].pop("K"), "pumps.doseLines.K.flowMlPerS must be a number > 0"),
        # T1-31: a zone's own pump rates, hardware.json pumps.zones
        ("hardware.json", lambda d: d["pumps"]["zones"]["Z2"]["doseLines"]["N"].update(flowMlPerS=0),
         "pumps.zones.Z2.doseLines.N.flowMlPerS must be a number > 0"),
        ("hardware.json", lambda d: d["pumps"]["zones"].update(Z9={"doseLines": {}}), "pumps.zones.Z9: layout.json has no zone Z9"),
        ("hardware.json", lambda d: d["pumps"]["zones"]["Z2"]["doseLines"].update(ACID={"flowMlPerS": 1}),
         "pumps.zones.Z2.doseLines.ACID: zone Z2 has no dose line ACID"),
        ("hardware.json", lambda d: d["pumps"]["zones"]["Z2"].pop("doseLines"), 'pumps.zones.Z2: needs a "doseLines" object'),
        ("hardware.json", lambda d: d["pumps"].update(zones=[]), "pumps.zones must be an object"),
        ("hardware.json", lambda d: d["pumps"]["zones"]["Z2"]["doseLines"]["N"].update(flowMlPerS=0.001),
         "controllers.ZC2: line N's largest dose, 2 mL at 0.001 mL/s, is 2000 s of pumping"),
    ],
)
def test_a_broken_pack_fails_and_says_why(tmp_path, name, change, said):
    assert said in broken(tmp_path, name, change)

def limits(data, zone_id="Z1"):
    return data["zones"][zone_id]["limits"]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda d: limits(d)["maxPerDoseMl"].update(N=-50), "zone Z1: limits.maxPerDoseMl.N must be mL > 0, got -50"),
        (lambda d: limits(d)["maxPerDoseMl"].update(P=0), "zone Z1: limits.maxPerDoseMl.P must be mL > 0, got 0"),
        (lambda d: limits(d)["maxPerDoseMl"].update(K="lots"), "zone Z1: limits.maxPerDoseMl.K must be mL > 0, got 'lots'"),
        (lambda d: limits(d, "Z2")["max24hMl"].update(N=-8), "zone Z2: limits.max24hMl.N must be mL > 0, got -8"),
        (lambda d: limits(d)["maxPerDoseMl"].update(K=9), "zone Z1: limits.maxPerDoseMl.K 9 mL is more than max24hMl.K 8 mL"),
        (lambda d: limits(d).update(ecCeiling=0), "zone Z1: limits.ecCeiling must be a number > 0, got 0"),
        (lambda d: limits(d).update(ecCeiling=-1.2), "zone Z1: limits.ecCeiling must be a number > 0, got -1.2"),
        (lambda d: limits(d).update(phMin=-1), "zone Z1: limits.phMin must be a number within 0-14, got -1"),
        (lambda d: limits(d).update(phMax=15), "zone Z1: limits.phMax must be a number within 0-14, got 15"),
        (lambda d: limits(d).update(phMin=7), "zone Z1: limits.phMin 7 must be below phMax 6.5"),
        (lambda d: limits(d).update(phMin=6.5), "zone Z1: limits.phMin 6.5 must be below phMax 6.5"),
        (lambda d: limits(d).update(minReservoirPctToDose=-5),
         "zone Z1: limits.minReservoirPctToDose must be a number within 0-100, got -5"),
        (lambda d: limits(d).update(minReservoirPctToDose=120),
         "zone Z1: limits.minReservoirPctToDose must be a number within 0-100, got 120"),
        (lambda d: d["zones"]["Z1"]["dose"].update(mixWaitS=-1),
         "zone Z1: dose.mixWaitS must be a number of seconds, not negative, got -1"),
        (lambda d: d["zones"]["Z2"]["dose"].update(maxDurationS=-600),
         "zone Z2: dose.maxDurationS must be a number of seconds, not negative, got -600"),
    ],
)
def test_a_limit_that_makes_no_sense_fails_and_says_why(tmp_path, change, said):
    """DM-9: limits.json values, not only its keys."""
    assert said in broken(tmp_path, "limits.json", change)


def test_limits_at_their_edges_are_valid(tmp_path):
    """The bounds themselves are allowed: per-dose equal to the 24 h limit, pH 0 and 14, reservoir
    0 % and 100 %, no mix wait."""
    clients = tmp_path / "clients"
    pack = shutil.copytree(CLIENTS / "client_B", clients / "client_B")
    data = json.loads((pack / "limits.json").read_text(encoding="utf-8"))
    limits(data)["maxPerDoseMl"]["N"] = limits(data)["max24hMl"]["N"]
    limits(data).update(phMin=0, phMax=14, minReservoirPctToDose=0)
    limits(data, "Z2")["minReservoirPctToDose"] = 100
    data["zones"]["Z1"]["dose"]["mixWaitS"] = 0
    (pack / "limits.json").write_text(json.dumps(data), encoding="utf-8")
    result = run(clients)
    assert result.returncode == 0, result.stderr

def test_a_climate_source_must_be_live_or_simulated(tmp_path):
    said = broken(tmp_path, "layout.json", lambda layout: layout["climate"].update(source="REAL"))
    assert "climate.source must be LIVE or SIMULATED" in said


def test_a_simulated_climate_needs_air_sensor_positions(tmp_path):
    said = broken(tmp_path, "layout.json", lambda layout: layout["climate"].update(airSensors=[]))
    assert "climate.airSensors" in said
