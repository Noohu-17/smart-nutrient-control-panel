"""The farm model reads its numbers from the config pack (T3-2).

The first group checks the reader with an inline config. The second group
checks the real config/clients/client_A pack and is skipped when the pack is
not there (for example outside the repository).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

import pytest

from sim.model.model_config import initial_reservoir, parse_zone_model
from sim.model.water import dose, readings, refill_flow_lps, reservoir_from_readings

ZONE = {  # contract 2.4, zone Z1
    "id": "Z1",
    "name": "Zone A",
    "controller": "ZC1",
    "reservoir": {"id": "R1", "usableL": 1200},
    "doseLines": ["A", "B", "ACID"],
}

MODEL: dict[str, Any] = {
    "ecPerGramPerL": 1.0,
    "rawWater": {"ecMsCm": 0.3, "ph": 7.2, "tempC": 26.0},
    "doseLines": {
        "A": {"nutrientGPerMl": 0.5, "phChangePerMlPer1000L": 0.0},
        "B": {"nutrientGPerMl": 0.5, "phChangePerMlPer1000L": 0.0},
        "ACID": {"nutrientGPerMl": 0.0, "phChangePerMlPer1000L": -0.006},
    },
    "refill": {"flowLpm": 30.0},
    "phDrift": {"targetPh": 7.2, "timeConstantH": 168.0},
    "solutionTemp": {"airCouplingWPerK": 230.0},
    "plantUptake": {
        "referenceAirTempC": 25.0,
        "waterChangePerDegC": 0.03,
        "crops": {
            "LETTUCE": {"GROWING": {"waterLPerBenchPerH": 1.5, "nutrientGPerBenchPerH": 3.0}},
        },
    },
    "initialState": {"reservoirPct": 50, "ecMsCm": 1.5, "ph": 6.0, "solTempC": 24.0},
    "numerics": {"maxSubstepS": 60, "maxSubstepsPerCall": 1000},
}


def model_with(path: str, value: Any) -> dict[str, Any]:
    """A copy of MODEL with one dotted key replaced (or removed when value is ...)."""
    cfg = copy.deepcopy(MODEL)
    *parents, last = path.split(".")
    node = cfg
    for key in parents:
        node = node[key]
    if value is ...:
        del node[last]
    else:
        node[last] = value
    return cfg


def test_reads_every_value_into_the_model():
    zone = parse_zone_model(MODEL, ZONE)

    assert zone.zone_id == "Z1"
    assert zone.reservoir.capacity_l == 1200.0
    assert zone.reservoir.raw_water.ec_ms_cm == 0.3
    assert zone.reservoir.dose_lines["ACID"].ph_change_per_ml_per_1000l == -0.006
    assert zone.reservoir.ph_drift_time_constant_h == 168.0
    assert zone.plants.crops["LETTUCE"]["GROWING"].water_l_per_bench_h == 1.5
    assert zone.reservoir.max_substeps == 1000
    assert refill_flow_lps(zone.reservoir) == pytest.approx(0.5)


def test_initial_reservoir_comes_from_the_config():
    zone = parse_zone_model(MODEL, ZONE)
    view = readings(initial_reservoir(zone), zone.reservoir)

    assert view.volume_l == pytest.approx(600.0)
    assert view.ec_ms_cm == pytest.approx(1.5)
    assert view.ph == 6.0


def test_dosing_lines_follow_the_layout_not_a_fixed_list():
    two_lines = {**ZONE, "doseLines": ["A", "B"]}
    zone = parse_zone_model(MODEL, two_lines)
    assert set(zone.reservoir.dose_lines) == {"A", "B"}

    with_base = {**ZONE, "doseLines": ["A", "B", "ACID", "BASE"]}
    with pytest.raises(ValueError, match=r"model\.doseLines\.BASE"):
        parse_zone_model(MODEL, with_base)


def test_unknown_keys_are_ignored():
    cfg = model_with("notes", {"ecPerGramPerL": "anything"})
    cfg["rawWater"]["note"] = "anything"
    parse_zone_model(cfg, ZONE)


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("ecPerGramPerL", ..., r"model\.ecPerGramPerL"),
        ("ecPerGramPerL", True, r"model\.ecPerGramPerL"),
        ("ecPerGramPerL", "1.0", r"model\.ecPerGramPerL"),
        ("ecPerGramPerL", 0.0, r"model: ec_per_g_per_l"),
        ("rawWater.ph", 15.0, r"model\.rawWater"),
        ("rawWater.ecMsCm", math.nan, r"model\.rawWater\.ecMsCm"),
        ("doseLines.A.nutrientGPerMl", -0.1, r"model\.doseLines\.A"),
        ("refill.flowLpm", -1.0, r"refill_flow_lpm"),
        ("phDrift", None, r"model\.phDrift"),
        ("plantUptake.crops.LETTUCE.GROWING.waterLPerBenchPerH", -1, r"LETTUCE\.GROWING"),
        ("initialState.reservoirPct", 120, r"model\.initialState"),
        ("numerics.maxSubstepsPerCall", 1.5, r"model\.numerics\.maxSubstepsPerCall"),
    ],
)
def test_bad_values_are_reported_with_their_key(path, value, message):
    with pytest.raises(ValueError, match=message):
        parse_zone_model(model_with(path, value), ZONE)


def test_layout_problems_are_reported():
    with pytest.raises(ValueError, match="usableL"):
        parse_zone_model(MODEL, {**ZONE, "reservoir": {"id": "R1"}})
    with pytest.raises(ValueError, match="doseLines"):
        parse_zone_model(MODEL, {**ZONE, "doseLines": "A,B"})


# --- The real client pack -----------------------------------------------------------

PACK = Path(__file__).resolve().parents[3] / "config" / "clients" / "client_A"
STATUSES = ("PLACEHOLDER", "PROTOTYPE", "CONTRACT-EXAMPLE", "NUMERICAL", "VERIFIED")


@pytest.fixture
def pack() -> tuple[dict[str, Any], dict[str, Any]]:
    simulator_json, layout_json = PACK / "simulator.json", PACK / "layout.json"
    if not (simulator_json.is_file() and layout_json.is_file()):
        pytest.skip(f"client pack not found at {PACK}")
    simulator = json.loads(simulator_json.read_text(encoding="utf-8"))
    if not isinstance(simulator.get("model"), dict):
        pytest.fail("simulator.json has no 'model' block for the farm model")
    return simulator, json.loads(layout_json.read_text(encoding="utf-8"))


def test_client_pack_parses_for_every_zone(pack):
    simulator, layout = pack
    assert layout["zones"], "layout.json lists no zones"
    for zone_cfg in layout["zones"]:
        zone = parse_zone_model(simulator["model"], zone_cfg)
        initial_reservoir(zone)


def covering_key(path: str, keys: Any) -> str | None:
    """The most specific dotted key that equals path or is a prefix of it."""
    covering = [k for k in keys if path == k or path.startswith(k + ".")]
    return max(covering, key=len) if covering else None


def test_every_client_pack_value_says_where_it_came_from(pack):
    # config/clients/README.md: placeholder status lives in _meta, with
    # placeholderKeys as dotted paths. T3-2 adds _meta.sources: one note per value.
    simulator, _ = pack
    meta = simulator["_meta"]
    sources, placeholder_keys = meta.get("sources", {}), meta.get("placeholderKeys", [])

    def leaves(node: Any, prefix: str) -> list[str]:
        if isinstance(node, dict):
            return [p for k, v in node.items() for p in leaves(v, f"{prefix}{k}.")]
        return [prefix.rstrip(".")] if isinstance(node, (int, float)) else []

    for path in leaves(simulator["model"], "model."):
        source = covering_key(path, sources)
        assert source, f"{path} has no note in _meta.sources"
        status = sources[source].split(":", 1)[0]
        assert status in STATUSES, f"{path}: unknown status {status!r}"
        if status not in ("NUMERICAL", "VERIFIED"):
            assert covering_key(path, placeholder_keys), f"{path} missing from placeholderKeys"
    assert meta["placeholder"] is True, "unverified farm values: the file is a placeholder"


def test_client_pack_dose_has_the_contract_example_effect(pack):
    # Contract 6.6 example: A 120 + B 120 + ACID 30 mL into a 936 L reservoir
    # moved EC 1.51 -> 1.63 and pH 6.21 -> 6.02. The configured stocks should
    # give the same direction and roughly the same size.
    simulator, layout = pack
    model = simulator["model"]
    zone_cfg = next(z for z in layout["zones"] if {"A", "B", "ACID"} <= set(z["doseLines"]))
    params = parse_zone_model(model, zone_cfg).reservoir
    start = reservoir_from_readings(936.0, 1.51, 6.21, 23.4, params)

    after = start
    for line, ml in (("A", 120.0), ("B", 120.0), ("ACID", 30.0)):
        after = dose(after, line, ml, params)
    before_view, after_view = readings(start, params), readings(after, params)

    assert after_view.ec_ms_cm - before_view.ec_ms_cm == pytest.approx(0.12, abs=0.03)
    assert after_view.ph - before_view.ph == pytest.approx(-0.19, abs=0.05)
