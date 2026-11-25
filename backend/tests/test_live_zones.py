"""Live zones on the back end (T1-33, contract §2.4, §11.2, §11.4).

A zone's source in layout.json is LIVE (real hardware runs it, such as the prototype rig) or
SIMULATED (the simulator plays it). Left out, it is SIMULATED on a playground site and LIVE
everywhere else. A live zone runs on real time, says LIVE in the zone API, and refuses the
simulator's holds, drills and resets, even on a playground site where its neighbours are simulated.
"""

import copy
import shutil
from datetime import UTC, datetime

import pytest
from test_dose_requests import (
    ORG,
    SITE,
    check,
    cmd_state,
    handle,
    ready_zone,
    review,
    sim_ago,
    write_dosing_pack,
)
from test_read_api import FEATURES, LAYOUT, api_client, assert_error, write_pack
from test_sim_forward import HOLD, NO_DATABASE, REPO_PACK, FakeSimulator, client

from app.api.pack import SiteRef, read_farm
from app.api.sim.routes import record_reset
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker

REAL_FARM = {"playground": False, "demoFarm": False}


def mixed_layout() -> dict:
    """The test layout with Z1 LIVE and Z2 left out, so SIMULATED on a playground site."""
    layout = copy.deepcopy(LAYOUT)
    layout["zones"][0]["source"] = "LIVE"
    return layout


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


# --- The config: LIVE or SIMULATED (§2.4) ----------------------------------------------------


def test_a_zone_without_a_source_is_simulated_on_a_playground_and_live_on_a_real_farm(tmp_path):
    playground = read_farm(write_pack(tmp_path / "playground"))
    real_farm = read_farm(write_pack(tmp_path / "farm", features=REAL_FARM))
    mixed = read_farm(write_pack(tmp_path / "mixed", mixed_layout()))
    assert [zone.source for zone in playground.zones] == ["SIMULATED", "SIMULATED"]
    assert [zone.source for zone in real_farm.zones] == ["LIVE", "LIVE"]  # never simulated by mistake
    assert [zone.source for zone in mixed.zones] == ["LIVE", "SIMULATED"]


def test_a_source_that_is_neither_is_a_broken_pack(tmp_path):
    layout = mixed_layout()
    layout["zones"][0]["source"] = "REAL"
    response = api_client(write_pack(tmp_path / ORG, layout), NO_DATABASE).get("/api/farm")
    assert_error(response, 500, "INTERNAL")


def test_the_farm_answer_gives_every_zone_its_source(tmp_path):
    body = api_client(write_pack(tmp_path / ORG, mixed_layout()), NO_DATABASE).get("/api/farm").json()
    assert {zone["id"]: zone["source"] for zone in body["zones"]} == {"Z1": "LIVE", "Z2": "SIMULATED"}


def test_client_a_is_a_simulated_farm():
    assert {zone.id: zone.source for zone in read_farm(REPO_PACK).zones} == {"Z1": "SIMULATED", "Z2": "SIMULATED"}


# --- The zone API says it (§11.2) ------------------------------------------------------------


def test_the_zone_api_says_live_or_simulated(tmp_path, migrated_db):
    pack = write_pack(tmp_path / ORG, mixed_layout())
    seed(migrated_db, pack)
    api = api_client(pack, migrated_db)
    zones = api.get("/api/zones").json()["items"]
    assert {zone["zone"]: zone["source"] for zone in zones} == {"Z1": "LIVE", "Z2": "SIMULATED"}
    assert api.get("/api/zones/Z1").json()["source"] == "LIVE"
    assert api.get("/api/zones/Z2").json()["source"] == "SIMULATED"


# --- Real time for a live zone (§11.4) -------------------------------------------------------


@pytest.fixture
def live_api(tmp_path, migrated_db):
    """A playground site whose Z1 is LIVE, with client_A's dosing files and a fresh Z1."""
    pack = write_dosing_pack(tmp_path / ORG, mixed_layout())
    seed(migrated_db, pack)
    ready_zone(Worker(migrated_db, ORG, SITE))
    return api_client(pack, migrated_db), Worker(migrated_db, ORG, SITE)


def test_a_live_zone_counts_its_24_hours_on_real_time_even_on_a_playground_site(live_api):
    api, worker = live_api
    # simTs 25 hours back would not count for a simulated zone; ts a minute ago does here
    handle(worker, cmd_state({"A": 1400}, sim_ts=sim_ago(25)), "farm/siet-ph1/zone/Z1/cmd/state")
    assert check(review(api), "LIMIT_24H") == ("BLOCK", "Line A would reach 1520 mL of the 1500 mL 24 h limit")
    assert api.get("/api/zones/Z1/dose-limits").json()["lines"][0]["dosed24hMl"] == 1400


def test_a_playground_reset_never_clears_a_live_zones_24_hours(live_api, migrated_db):
    api, worker = live_api
    handle(worker, cmd_state({"A": 1400}), "farm/siet-ph1/zone/Z1/cmd/state")
    record_reset(migrated_db, SiteRef(ORG, SITE), datetime.now(UTC))  # say one was recorded anyway
    assert check(review(api), "LIMIT_24H")[0] == "BLOCK"  # what the real zone received still counts


# --- The simulator's controls refuse a live zone (§11.4) ------------------------------------


@pytest.fixture
def fake():
    simulator = FakeSimulator()
    yield simulator
    simulator.close()


@pytest.fixture
def sim_api(tmp_path, fake):
    """A playground site whose Z1 is LIVE and Z2 simulated, with the fake simulator."""
    pack = write_pack(tmp_path / ORG, mixed_layout(), FEATURES)
    shutil.copy(REPO_PACK / "scenarios.json", pack / "scenarios.json")
    return client(pack, fake.url)


REFUSED = [
    ("POST", "/api/sim/holds", HOLD),
    ("POST", "/api/sim/faults", {"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": True}),
    ("POST", "/api/sim/faults", {"kind": "DAYTANK_LOW", "target": "Z1.ACID", "on": True}),
    ("POST", "/api/sim/faults", {"kind": "LEAK", "target": "Z1", "on": False}),
    ("POST", "/api/sim/faults", {"kind": "DEVICE_OFFLINE", "target": "ZC1", "on": True}),
    ("DELETE", "/api/sim/holds/Z1/ph", None),
]


@pytest.mark.parametrize(("method", "path", "body"), REFUSED, ids=[f"{r[0]} {r[1]} {r[2]}" for r in REFUSED])
def test_a_hold_or_drill_on_a_live_zone_is_refused_and_never_sent(sim_api, fake, method, path, body):
    response = sim_api.request(method, path, json=body)
    error = assert_error(response, 409, "LIVE_ZONE")
    assert error["message"] == "Z1 is a live zone: real hardware runs it, not the simulator"
    assert fake.calls == []


def test_a_reset_is_refused_while_any_zone_is_live(sim_api, fake):
    error = assert_error(sim_api.post("/api/sim/reset"), 409, "LIVE_ZONE")
    assert error["message"] == "This site has live zones (Z1): a reset is only for a simulated farm"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/sim/holds", HOLD | {"target": "Z2"}),
        ("POST", "/api/sim/holds", {"target": "POLYHOUSE", "metric": "airTempC", "value": 35}),
        ("POST", "/api/sim/faults", {"kind": "SENSOR_OFFLINE", "target": "Z2.ph", "on": True}),
        ("POST", "/api/sim/faults", {"kind": "DEVICE_OFFLINE", "target": "ZC2", "on": True}),
        ("POST", "/api/sim/faults", {"kind": "HEATWAVE", "target": "POLYHOUSE", "on": True}),
        ("POST", "/api/sim/speed", {"x": 288}),  # only the simulated zones' clock
        ("POST", "/api/sim/holds/release-all", None),
        ("GET", "/api/sim/state", None),
    ],
)
def test_the_simulated_zones_and_the_polyhouse_still_reach_the_simulator(sim_api, fake, method, path, body):
    response = sim_api.request(method, path, json=body)
    assert response.status_code == 200, response.text
    assert len(fake.calls) == 1


def test_a_hold_on_a_simulated_zone_can_still_be_released(sim_api, fake):
    assert sim_api.post("/api/sim/holds", json=HOLD | {"target": "Z2"}).status_code == 200
    assert sim_api.delete("/api/sim/holds/Z2/ph").status_code == 200


def test_a_site_with_no_playground_still_answers_404_first(tmp_path, fake):
    """client_B, a real farm: no simulator at all, so 404 before any zone is looked at."""
    pack = write_pack(tmp_path / ORG, LAYOUT, REAL_FARM)
    response = client(pack, fake.url).post("/api/sim/holds", json=HOLD)
    assert_error(response, 404, "NOT_FOUND")
    assert fake.calls == []
