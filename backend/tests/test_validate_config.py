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
    ],
)
def test_a_broken_pack_fails_and_says_why(tmp_path, name, change, said):
    assert said in broken(tmp_path, name, change)
