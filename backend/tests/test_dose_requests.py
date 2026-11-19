"""POST /api/dose-requests (T1-16): the §11.3 safety review on the server.

Messages go in through the worker, as in test_latest_state. The config pack is the test layout
plus client_A's own limits, recipes, hardware, simulator and alerts files, so the numbers the
checks use come from the config, as in production.
"""

import asyncio
import dataclasses
import json
import re
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_read_api import CONTRACT, LAYOUT, ago, api_client, assert_error, write_pack
from test_worker import SIM_TS, envelope, heartbeat, zone_state, zone_telemetry

from app.api.dosing import _number, _round
from app.api.routes import _engine_for
from app.api.schemas import contract_time
from app.config import load_settings
from app.db.models import CommandReport, DoseRequest, LatestMessage
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker

ORG = "client_A"
SITE = LAYOUT["site"]
CLIENT_A = Path(__file__).resolve().parents[2] / "config" / "clients" / "client_A"
DOSING_FILES = ("limits.json", "recipes.json", "hardware.json", "nutrients.json", "alerts.json")
CHECK_IDS = [
    "STATE_KNOWN", "DEVICES_ONLINE", "ESTOP", "ZONE_REMOTE", "NO_CONFLICT", "LIMIT_DOSE",
    "LIMIT_24H", "DAYTANK", "RESERVOIR", "PROJECTED_EC_PH", "FRESH_READINGS",
]
DOSE = {"zone": "Z1", "lines": [{"line": "A", "ml": 120}, {"line": "B", "ml": 120}, {"line": "ACID", "ml": 30}]}
OUTPUTS = {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF", "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}
READINGS = zone_telemetry()["readings"] | {"ph": 6.04}
QUALITY = {"ec": "OK", "ph": "OK", "solTempC": "OK", "reservoirPct": "OK", "circFlowLpm": "OK", "dayTankPct": "OK"}


def write_dosing_pack(folder: Path, layout: dict = LAYOUT, features: dict | None = None) -> Path:
    pack = write_pack(folder, layout, **({"features": features} if features else {}))
    for name in DOSING_FILES:
        shutil.copy(CLIENT_A / name, pack / name)
    return pack


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def pack(tmp_path):
    return write_dosing_pack(tmp_path / ORG)


@pytest.fixture
def seeded(migrated_db, pack):
    seed(migrated_db, pack)
    return migrated_db


@pytest.fixture
def worker(seeded):
    return Worker(seeded, ORG, SITE)


@pytest.fixture
def api(seeded, pack):
    return api_client(pack, seeded)


def handle(worker, message, topic="farm/siet-ph1/zone/Z1/state"):
    asyncio.run(worker.handle(topic, json.dumps(message).encode()))


def now_ts(seconds_ago=1):
    return contract_time(ago(seconds_ago))


def telemetry(seconds_ago=5, readings=None, quality=None, **changes):
    return zone_telemetry(
        ts=now_ts(seconds_ago), readings=READINGS | (readings or {}), quality=QUALITY | (quality or {}), **changes
    )


def ready_zone(worker, *, state=None, readings=None, quality=None, age=5, seq=10):
    """A zone a dose could go to: REMOTE, nothing pressed or running, fresh OK readings."""
    handle(worker, telemetry(age, readings, quality, seq=seq), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, zone_state(ts=now_ts(1), seq=seq + 1, outputs=OUTPUTS) | (state or {}))


def cmd_state(delivered: dict, *, sim_ts=SIM_TS, cmd_id=None, seq=100, device="ZC1", state="COMPLETED", **changes):
    lines = [
        {"line": line, "requestedMl": ml, "deliveredMl": ml, "state": "DONE"} for line, ml in delivered.items()
    ]
    return envelope("CMD_STATE", device) | {
        "ts": now_ts(60), "simTs": sim_ts, "seq": seq, "cmdId": cmd_id or str(uuid.uuid4()), "state": state,
        "reason": None, "lines": lines, "before": None, "after": None, "startedAt": None, "endedAt": None,
    } | changes


def sim_ago(hours: float) -> str:
    """A simTs this many simulated hours before the zone telemetry's own simTs."""
    return contract_time(datetime.fromisoformat(SIM_TS) - timedelta(hours=hours))


def review(api, request=DOSE):
    response = api.post("/api/dose-requests", json=request)
    assert response.status_code == 201, response.text
    return response.json()


def check(body, check_id):
    found = next(c for c in body["checks"] if c["id"] == check_id)
    return found["result"], found["message"]


def contract_example() -> dict:
    text = CONTRACT.read_text(encoding="utf-8")
    after = text[text.index("### 11.3"):]
    return json.loads(re.search(r"```json\n(.*?)```", after, re.DOTALL).group(1))


# --- A dose that may go ahead --------------------------------------------------------------


def test_a_safe_dose_passes_every_check_in_the_contracts_order(api, worker):
    ready_zone(worker)
    body = review(api)
    assert [c["id"] for c in body["checks"]] == CHECK_IDS
    assert {c["id"]: c["result"] for c in body["checks"]} == dict.fromkeys(CHECK_IDS, "PASS"), body["checks"]
    assert body["canConfirm"] is True
    assert body["id"].startswith("dr_")
    assert (body["zone"], body["lines"]) == ("Z1", DOSE["lines"])


def test_the_messages_read_as_the_apps_fake(api, worker):
    ready_zone(worker)
    body = review(api)
    assert check(body, "STATE_KNOWN")[1] == "Zone 1 state known: nothing unconfirmed, not stopped"
    assert check(body, "DEVICES_ONLINE")[1] == "ZC1 and sensors online"
    assert check(body, "ESTOP")[1] == "No E-STOP pressed"
    assert check(body, "ZONE_REMOTE")[1] == "Zone 1 is in REMOTE"
    assert check(body, "NO_CONFLICT")[1] == "Nothing else running"
    assert check(body, "LIMIT_DOSE")[1] == "All lines within per-dose limits"
    assert check(body, "LIMIT_24H")[1] == "24 h totals within limits"
    assert check(body, "DAYTANK")[1] == "Enough concentrate in every day-tank"
    assert check(body, "RESERVOIR")[1] == "R1 at 78%"
    assert re.fullmatch(r"EC/pH \d+ s old", check(body, "FRESH_READINGS")[1])


def test_the_projection_and_estimate_come_from_the_config_pack(api, worker):
    ready_zone(worker)
    body = review(api)
    # nutrients.json: 240 mL of A and B at 0.47 per mL per L into 936 L adds 0.12; 30 mL of acid takes 0.19 off pH
    assert body["projected"] == {"ec": 1.74, "ph": 5.9}
    # recipes.json: Z1 is LETTUCE GROWING, EC 1.2–1.8 and pH 5.6–6.2
    assert check(body, "PROJECTED_EC_PH") == ("PASS", "Projected EC 1.74, pH 5.9 (target 1.2–1.8, 5.6–6.2)")
    # hardware.json: every pump at 2 mL/s, so 270 mL takes 2.25 min; limits.json: 3000 s mix wait
    assert body["estimate"] == {"durationMin": 53, "totalMl": 270}


def test_the_projection_follows_each_lines_stock_in_nutrients_json(tmp_path, migrated_db):
    pack = write_dosing_pack(tmp_path / ORG)
    nutrients = json.loads((pack / "nutrients.json").read_text(encoding="utf-8"))
    nutrients["doseLines"]["A"]["ecChangePerMlPerL"] = 0.94  # a stock twice as strong
    nutrients["doseLines"]["ACID"]["phChangePerMlPerL"] = 0.0  # a line that leaves pH alone
    (pack / "nutrients.json").write_text(json.dumps(nutrients), encoding="utf-8")
    seed(migrated_db, pack)
    ready_zone(Worker(migrated_db, ORG, SITE))
    # 120 mL x 0.94 + 120 mL x 0.47 into 936 L adds 0.18 to EC 1.62; pH stays at 6.04
    assert review(api_client(pack, migrated_db))["projected"] == {"ec": 1.8, "ph": 6.0}


def test_the_answer_has_the_fields_of_the_contract_example(api, worker):
    ready_zone(worker)
    body = review(api)
    example = contract_example()
    assert list(body) == list(example)
    assert list(body["estimate"]) == list(example["estimate"])
    assert all(list(c) == ["id", "result", "message"] for c in body["checks"])


def test_the_request_is_saved_for_the_confirm_step(api, worker, seeded):
    ready_zone(worker)
    body = review(api)
    with seeded.connect() as connection:
        row = connection.execute(select(DoseRequest).where(DoseRequest.request_id == body["id"])).one()
    assert (row.org_id, row.site_id, row.zone_id, row.can_confirm) == (ORG, SITE, "Z1", True)
    assert row.checks == body["checks"] and row.estimate == body["estimate"] and row.lines == body["lines"]
    assert row.limits["maxPerDoseMl"] == {"A": 300, "B": 300, "ACID": 100}  # the snapshot a confirm sends (§7.2)


def test_a_review_changes_nothing_on_the_farm_side(api, worker, seeded):
    ready_zone(worker)
    with seeded.connect() as connection:
        before = connection.execute(select(LatestMessage.body).order_by(LatestMessage.type)).all()
    review(api)
    with seeded.connect() as connection:
        assert connection.execute(select(LatestMessage.body).order_by(LatestMessage.type)).all() == before
        assert connection.scalar(select(func.count()).select_from(CommandReport)) == 0


# --- Each thing that blocks it -----------------------------------------------------------


def test_an_estop_blocks(api, worker):
    ready_zone(worker, state={"estop": True})
    body = review(api)
    assert check(body, "ESTOP") == ("BLOCK", "E-STOP pressed in Zone 1")
    assert body["canConfirm"] is False


@pytest.mark.parametrize("mode", ["LOCAL", "MAINT"])
def test_a_zone_not_in_remote_blocks(api, worker, mode):
    ready_zone(worker, state={"mode": mode})
    body = review(api)
    assert check(body, "ZONE_REMOTE") == ("BLOCK", f"Zone 1 key switch is in {mode}")
    assert body["canConfirm"] is False


def test_an_over_limit_dose_blocks(api, worker):
    ready_zone(worker)
    body = review(api, {"zone": "Z1", "lines": [{"line": "A", "ml": 301}, {"line": "ACID", "ml": 101}]})
    assert check(body, "LIMIT_DOSE") == (
        "BLOCK", "Line A exceeds 300 mL per dose; Line ACID exceeds 100 mL per dose",
    )
    assert body["canConfirm"] is False


def test_a_dose_at_exactly_the_limit_passes(api, worker):
    ready_zone(worker)
    body = review(api, {"zone": "Z1", "lines": [{"line": "A", "ml": 300}]})
    assert check(body, "LIMIT_DOSE")[0] == "PASS"


def test_a_stale_reading_blocks(api, worker):
    ready_zone(worker, age=200)
    handle(worker, heartbeat(ts=now_ts(1), seq=99), "farm/siet-ph1/device/ZC1/status")  # ZC1 still online
    body = review(api)
    assert check(body, "DEVICES_ONLINE")[0] == "PASS"
    result, message = check(body, "FRESH_READINGS")
    assert result == "BLOCK"
    assert re.fullmatch(r"EC/pH 20\d s old, more than 120 s", message)
    assert body["canConfirm"] is False


@pytest.mark.parametrize(
    ("quality", "why"),
    [("FAULT", "sensor fault"), ("OUT_OF_RANGE", "outside the sensor's range"),
     ("UNCALIBRATED", "sensor needs calibrating")],
)
def test_a_ph_reading_that_isnt_ok_blocks_in_plain_words(api, worker, quality, why):
    ready_zone(worker, quality={"ph": quality}, readings={"ph": None} if quality == "FAULT" else None)
    assert check(review(api), "FRESH_READINGS") == ("BLOCK", f"pH reading not usable: {why}")


def test_a_reading_that_stopped_changing_is_not_updating_not_offline(api, worker):
    ready_zone(worker, quality={"ph": "STALE"})  # what the controller sends for a stuck probe
    body = review(api)
    assert check(body, "DEVICES_ONLINE")[0] == "PASS"
    assert check(body, "FRESH_READINGS") == ("BLOCK", "pH reading is not updating")


def test_an_unknown_state_blocks(api, worker):
    handle(worker, telemetry(), "farm/siet-ph1/zone/Z1/telemetry")  # readings, but no ZONE_STATE yet
    body = review(api)
    assert check(body, "STATE_KNOWN") == ("BLOCK", "Zone 1 state not known yet")
    assert check(body, "ESTOP") == ("BLOCK", "E-STOP state of Zone 1 not known yet")
    assert check(body, "ZONE_REMOTE") == ("BLOCK", "Zone 1 key switch is in an unknown position")
    assert body["canConfirm"] is False


def test_a_zone_that_has_never_reported_blocks_on_everything_it_cant_know(api):
    body = review(api)
    results = {c["id"]: c["result"] for c in body["checks"]}
    for check_id in ("STATE_KNOWN", "DEVICES_ONLINE", "ESTOP", "ZONE_REMOTE", "DAYTANK", "RESERVOIR",
                     "PROJECTED_EC_PH", "FRESH_READINGS"):
        assert results[check_id] == "BLOCK", check_id
    assert check(body, "DEVICES_ONLINE") == ("BLOCK", "ZC1 has never reported")
    assert check(body, "PROJECTED_EC_PH") == ("BLOCK", "Cannot project EC and pH without both readings")
    assert check(body, "FRESH_READINGS") == ("BLOCK", "No EC or pH reading yet")
    assert body["projected"] == {"ec": None, "ph": None}


@pytest.mark.parametrize(
    ("state", "telemetry_changes", "message"),
    [
        ({"stopped": True}, {}, "Zone 1 is stopped. Resume the zone first"),
        ({"latches": ["LOW_LOW"]}, {}, "Zone 1 has a latched fault to clear: LOW_LOW"),
        ({}, {"activeCmdId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44"}, "Zone 1 has a command still in flight"),
    ],
    ids=["stopped", "latched", "in-flight"],
)
def test_a_zone_whose_state_isnt_clear_blocks(api, worker, state, telemetry_changes, message):
    handle(worker, telemetry(**telemetry_changes), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, zone_state(ts=now_ts(1), seq=50, outputs=OUTPUTS) | state)
    assert check(review(api), "STATE_KNOWN") == ("BLOCK", message)


def test_the_last_will_blocks_at_once(api, worker):
    ready_zone(worker, age=2)
    handle(worker, {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False}, "farm/siet-ph1/device/ZC1/status")
    body = review(api)
    assert check(body, "DEVICES_ONLINE") == ("BLOCK", "ZC1 has gone offline")
    assert check(body, "FRESH_READINGS")[0] == "BLOCK"
    assert check(body, "FRESH_READINGS")[1].endswith("but ZC1 is offline")


def test_a_controller_silent_for_three_minutes_blocks(api, worker):
    handle(worker, telemetry(200), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, zone_state(ts=now_ts(200), seq=50, outputs=OUTPUTS))
    body = review(api)
    result, message = check(body, "DEVICES_ONLINE")
    assert result == "BLOCK" and re.fullmatch(r"ZC1 not heard from for \d+ s", message)


@pytest.mark.parametrize(
    "state",
    [{"outputs": OUTPUTS | {"Z1-DOSE-A": "ON"}}, {"outputs": OUTPUTS | {"Z1-REFILL": "OPEN"}},
     {"operation": {"cmdId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44", "kind": "REFILL", "state": "EXECUTING"}}],
    ids=["dose-pump-on", "refill-open", "operation"],
)
def test_another_operation_blocks(api, worker, state):
    ready_zone(worker, state=state)
    assert check(review(api), "NO_CONFLICT") == ("BLOCK", "Another dose or refill is running in Zone 1")


def test_not_enough_concentrate_blocks_and_a_low_tank_warns(api, worker):
    ready_zone(worker, readings={"dayTankL": {"A": 0.1, "B": 33.0, "ACID": 40.5}})
    body = review(api)
    assert check(body, "DAYTANK") == ("BLOCK", "Day-tank A has less than 120 mL")

    ready_zone(worker, readings={"dayTankPct": {"A": 64, "B": 18, "ACID": 81}}, seq=20)
    body = review(api)
    assert check(body, "DAYTANK") == ("WARN", "Day-tank B at 18%, refill soon")  # alerts.json DAYTANK_LOW 20 %
    assert body["canConfirm"] is True


def test_a_low_reservoir_blocks(api, worker):
    ready_zone(worker, readings={"reservoirPct": 20})
    assert check(review(api), "RESERVOIR") == ("BLOCK", "R1 at 20%, below the 30% minimum to dose")


def test_a_projection_outside_the_safety_limits_blocks(api, worker):
    ready_zone(worker, readings={"ec": 2.35})
    result, message = check(review(api), "PROJECTED_EC_PH")
    assert result == "BLOCK"
    assert message == "Projected EC 2.47, pH 5.9 is outside the safety limits (EC up to 2.4, pH 5.5–6.8)"


def test_a_projection_outside_the_target_warns(api, worker):
    ready_zone(worker, readings={"ec": 1.75})
    body = review(api)
    assert check(body, "PROJECTED_EC_PH") == ("WARN", "Projected EC 1.87, pH 5.9 (target 1.2–1.8, 5.6–6.2)")
    assert body["canConfirm"] is True


def test_a_reading_with_no_quality_isnt_dosed_on(api, worker):
    quality = {k: v for k, v in QUALITY.items() if k != "ph"}
    handle(worker, zone_telemetry(ts=now_ts(5), readings=READINGS, quality=quality), "farm/siet-ph1/zone/Z1/telemetry")
    handle(worker, zone_state(ts=now_ts(1), seq=50, outputs=OUTPUTS))
    assert check(review(api), "FRESH_READINGS") == (
        "BLOCK", "pH reading not usable: it came without a quality mark"
    )


def test_a_zone_with_no_recipe_applied_warns(tmp_path, migrated_db):
    pack = write_dosing_pack(tmp_path / ORG)
    recipes = json.loads((pack / "recipes.json").read_text(encoding="utf-8"))
    del recipes["zones"]["Z1"]
    (pack / "recipes.json").write_text(json.dumps(recipes), encoding="utf-8")
    seed(migrated_db, pack)
    ready_zone(Worker(migrated_db, ORG, SITE))
    result, message = check(review(api_client(pack, migrated_db)), "PROJECTED_EC_PH")
    assert (result, message) == ("WARN", "Projected EC 1.74, pH 5.9 (no recipe applied to Zone 1)")


# --- 24-hour totals ----------------------------------------------------------------------


def test_delivered_ml_in_the_last_24_simulated_hours_counts(api, worker):
    ready_zone(worker)
    handle(worker, cmd_state({"A": 900, "B": 100}, sim_ts=sim_ago(23)), "farm/siet-ph1/zone/Z1/cmd/state")
    handle(worker, cmd_state({"A": 500}, sim_ts=sim_ago(1), seq=101), "farm/siet-ph1/zone/Z1/cmd/state")
    body = review(api)
    assert check(body, "LIMIT_24H") == ("BLOCK", "Line A would reach 1520 mL of the 1500 mL 24 h limit")


def test_doses_older_than_24_simulated_hours_dont_count(api, worker):
    ready_zone(worker)
    handle(worker, cmd_state({"A": 1400}, sim_ts=sim_ago(25)), "farm/siet-ph1/zone/Z1/cmd/state")
    assert check(review(api), "LIMIT_24H")[0] == "PASS"


def test_a_real_farm_counts_on_real_time(tmp_path, migrated_db):
    pack = write_dosing_pack(tmp_path / ORG, features={"playground": False, "demoFarm": False})
    seed(migrated_db, pack)
    worker = Worker(migrated_db, ORG, SITE)
    ready_zone(worker)
    # simTs 25 hours back would not count on a playground site; ts a minute ago does here
    handle(worker, cmd_state({"A": 1400}, sim_ts=sim_ago(25)), "farm/siet-ph1/zone/Z1/cmd/state")
    assert check(review(api_client(pack, migrated_db)), "LIMIT_24H")[0] == "BLOCK"


# --- GET /api/zones/{zoneId}/dose-limits (T1-37) -----------------------------------------


def limits(api, zone="Z1"):
    response = api.get(f"/api/zones/{zone}/dose-limits")
    assert response.status_code == 200, response.text
    return {line["line"]: line for line in response.json()["lines"]}


def test_dose_limits_come_from_the_config_pack_with_nothing_dosed_yet(api, worker):
    ready_zone(worker)
    # limits.json: Z1 allows 300 mL per dose and 1500 mL a day of A and B, 100 and 400 of acid
    numbers = ("line", "maxPerDoseMl", "max24hMl", "dosed24hMl", "left24hMl")
    assert {line: {key: entry[key] for key in numbers} for line, entry in limits(api).items()} == {
        "A": {"line": "A", "maxPerDoseMl": 300, "max24hMl": 1500, "dosed24hMl": 0, "left24hMl": 1500},
        "B": {"line": "B", "maxPerDoseMl": 300, "max24hMl": 1500, "dosed24hMl": 0, "left24hMl": 1500},
        "ACID": {"line": "ACID", "maxPerDoseMl": 100, "max24hMl": 400, "dosed24hMl": 0, "left24hMl": 400},
    }


def test_dose_limits_say_what_each_line_doses_and_how_fast_its_pump_runs(api, worker):
    """T1-38: nutrients.json's stock and hardware.json's pump rate, for the dose form."""
    ready_zone(worker)
    acid = limits(api)["ACID"]
    stock = json.loads((CLIENT_A / "nutrients.json").read_text(encoding="utf-8"))["doseLines"]["ACID"]
    flow = json.loads((CLIENT_A / "hardware.json").read_text(encoding="utf-8"))["pumps"]["doseLines"]["ACID"]
    assert {key: acid[key] for key in ("name", "compound", "strengthPct", "drum", "purpose")} == {
        key: stock[key] for key in ("name", "compound", "strengthPct", "drum", "purpose")
    }
    assert acid["pumpMlPerMin"] == flow["flowMlPerS"] * 60


def test_dose_limits_of_a_pack_that_doesnt_describe_its_lines_are_null(tmp_path, migrated_db):
    pack = write_dosing_pack(tmp_path / ORG)
    nutrients = json.loads((pack / "nutrients.json").read_text(encoding="utf-8"))
    nutrients["doseLines"] = {
        line: {key: entry[key] for key in ("ecChangePerMlPerL", "phChangePerMlPerL")}
        for line, entry in nutrients["doseLines"].items()
    }
    (pack / "nutrients.json").write_text(json.dumps(nutrients), encoding="utf-8")
    seed(migrated_db, pack)
    line = limits(api_client(pack, migrated_db))["A"]
    assert [line[key] for key in ("name", "compound", "strengthPct", "drum", "purpose")] == [None] * 5
    assert line["pumpMlPerMin"] == 120  # hardware.json still gives the pump rate


def test_dose_limits_count_the_last_24_hours_as_the_review_does(api, worker):
    ready_zone(worker)
    handle(worker, cmd_state({"A": 900, "B": 100}, sim_ts=sim_ago(23)), "farm/siet-ph1/zone/Z1/cmd/state")
    handle(worker, cmd_state({"A": 800}, sim_ts=sim_ago(25), seq=101), "farm/siet-ph1/zone/Z1/cmd/state")
    lines = limits(api)
    assert (lines["A"]["dosed24hMl"], lines["A"]["left24hMl"]) == (900, 600)
    assert (lines["B"]["dosed24hMl"], lines["B"]["left24hMl"]) == (100, 1400)
    # one mL more than what is left is what the review blocks
    over = {"zone": "Z1", "lines": [{"line": "A", "ml": 601}]}
    assert check(review(api, over), "LIMIT_24H")[0] == "BLOCK"


def test_dose_limits_never_go_below_zero(api, worker):
    ready_zone(worker)
    handle(worker, cmd_state({"A": 1600}, sim_ts=sim_ago(1)), "farm/siet-ph1/zone/Z1/cmd/state")
    assert limits(api)["A"]["left24hMl"] == 0


@pytest.mark.parametrize("zone", ["Z9", "zone1"])
def test_dose_limits_of_an_unknown_zone_are_404(api, zone):
    assert_error(api.get(f"/api/zones/{zone}/dose-limits"), 404, "NOT_FOUND")


def test_dose_limits_before_the_farm_is_seeded_are_503(migrated_db, pack):
    assert_error(api_client(pack, migrated_db).get("/api/zones/Z1/dose-limits"), 503, "SERVICE_UNAVAILABLE")


def test_a_command_counts_once_by_its_newest_report(api, worker, seeded):
    ready_zone(worker)
    cmd_id = str(uuid.uuid4())
    for seq, ml, state in ((100, 400, "EXECUTING"), (102, 1390, "COMPLETED"), (101, 900, "EXECUTING")):
        handle(worker, cmd_state({"A": ml}, cmd_id=cmd_id, seq=seq, state=state), "farm/siet-ph1/zone/Z1/cmd/state")
    with seeded.connect() as connection:
        assert connection.scalar(select(CommandReport.state)) == "COMPLETED"
    assert check(review(api), "LIMIT_24H") == ("BLOCK", "Line A would reach 1510 mL of the 1500 mL 24 h limit")


def test_another_zones_doses_dont_count(api, worker):
    ready_zone(worker)
    handle(worker, cmd_state({"A": 1400}, device="ZC2"), "farm/siet-ph1/zone/Z2/cmd/state")
    assert check(review(api), "LIMIT_24H")[0] == "PASS"


def test_a_report_from_another_device_never_replaces_a_commands_report(worker, seeded):
    cmd_id = str(uuid.uuid4())
    handle(worker, cmd_state({"A": 100}, cmd_id=cmd_id, seq=5), "farm/siet-ph1/zone/Z1/cmd/state")
    handle(worker, cmd_state({"A": 999}, cmd_id=cmd_id, seq=500, device="ZC2"), "farm/siet-ph1/zone/Z2/cmd/state")
    with seeded.connect() as connection:
        row = connection.execute(select(CommandReport.device_id, CommandReport.body)).one()
    assert (row.device_id, row.body["lines"][0]["deliveredMl"]) == ("ZC1", 100)


# --- Bad requests and errors ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ([{"line": "C", "ml": 10}], "Zone 1 has no dosing line C"),
        ([{"line": "A", "ml": 12.5}], "Line A: enter whole mL"),
        ([{"line": "A", "ml": -5}], "Line A: enter whole mL"),
        ([{"line": "A", "ml": 0}, {"line": "B", "ml": 0}], "Enter an amount for at least one line"),
        ([], "Enter an amount for at least one line"),
        ([{"line": "A", "ml": 10}, {"line": "A", "ml": 10}], "Line A is given more than once"),
    ],
)
def test_amounts_that_cant_be_a_dose_are_422_invalid_command(api, lines, message):
    error = assert_error(api.post("/api/dose-requests", json={"zone": "Z1", "lines": lines}), 422, "INVALID_COMMAND")
    assert error["message"] == message


def test_a_line_at_zero_is_allowed_next_to_one_that_doses(api, worker):
    ready_zone(worker)
    body = review(api, {"zone": "Z1", "lines": [{"line": "A", "ml": 100}, {"line": "B", "ml": 0}]})
    assert body["estimate"]["totalMl"] == 100


@pytest.mark.parametrize(
    "body",
    [{"zone": "Z1"}, {"lines": []}, {"zone": "Z1", "lines": [{"line": "A", "ml": "100"}]}, [], "not json"],
)
def test_a_malformed_body_is_422_validation_error(api, body):
    assert_error(api.post("/api/dose-requests", json=body), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("zone", ["Z9", "Z1; DROP", "z1", ""])
def test_an_unknown_zone_is_404(api, zone):
    error = assert_error(api.post("/api/dose-requests", json=DOSE | {"zone": zone}), 404, "NOT_FOUND")
    assert error["message"] == f"There is no zone {zone}"


def test_a_farm_not_seeded_yet_is_503(migrated_db, pack):
    assert_error(api_client(pack, migrated_db).post("/api/dose-requests", json=DOSE), 503, "SERVICE_UNAVAILABLE")


def test_a_database_thats_down_is_503(pack):
    settings = dataclasses.replace(load_settings(), client_dir=pack, db_host="127.0.0.1", db_port=1)
    response = api_client(pack, _engine_for(settings)).post("/api/dose-requests", json=DOSE)
    assert_error(response, 503, "SERVICE_UNAVAILABLE")


@pytest.mark.parametrize("missing", DOSING_FILES)
def test_a_config_pack_missing_a_dosing_file_is_500(seeded, pack, missing):
    (pack / missing).unlink()
    assert_error(api_client(pack, seeded).post("/api/dose-requests", json=DOSE), 500, "INTERNAL")


def test_get_is_405(api):
    assert_error(api.get("/api/dose-requests"), 405, "METHOD_NOT_ALLOWED")


def test_the_schema_lists_the_route_with_the_contract_error_shape():
    from fastapi.testclient import TestClient

    route = TestClient(app).get("/api/openapi.json").json()["paths"]["/api/dose-requests"]["post"]
    assert "201" in route["responses"]
    for status in ("404", "422", "503"):
        assert route["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")


# --- The small rules -----------------------------------------------------------------------


@pytest.mark.parametrize(("value", "places", "rounded"), [(1.745, 2, 1.75), (5.85, 1, 5.9), (1.744, 2, 1.74)])
def test_rounding_is_half_up_as_in_the_app(value, places, rounded):
    assert _round(value, places) == rounded


@pytest.mark.parametrize(("value", "shown"), [(300, "300"), (300.0, "300"), (2.4, "2.4"), (1.5, "1.5")])
def test_numbers_print_as_the_app_prints_them(value, shown):
    assert _number(value) == shown
