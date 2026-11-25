"""Alerts for a sensor fault, a device offline and stale readings (T1-34, contract §11.2, §14).

Each condition is faked as the farm would show it, raised, and cleared when it recovers. Messages
go in through the worker, and the silence sweep runs with a given clock, so the delays and
thresholds come from client_A's alerts.json, as in production: SENSOR_FAULT waits 60 s,
DEVICE_OFFLINE is 180 s of silence with no delay, READING_STALE is 120 s with a 60 s delay.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from test_alerts import EVENTS, alerts, only
from test_dose_requests import ORG, SITE, handle, telemetry, write_dosing_pack
from test_event import event
from test_read_api import api_client
from test_worker import heartbeat

from app.alerts import FRESH_FOR_S, OFFLINE_AFTER_S, AlertRules
from app.api.pack import SiteRef
from app.api.sim.routes import record_reset
from app.db.models import Alert
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker
from app.worker.silence import sweep, watch

TELEMETRY = "farm/siet-ph1/zone/Z1/telemetry"
STATUS = "farm/siet-ph1/device/{}/status"


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
def rules(pack):
    return AlertRules.from_pack(pack)


@pytest.fixture
def worker(seeded, rules):
    return Worker(seeded, ORG, SITE, rules=rules)


@pytest.fixture
def api(seeded, pack):
    return api_client(pack, seeded)


class Farm:
    """Zone 1's controller as the worker hears it, and the sweep at any time from now."""

    def __init__(self, worker, engine, rules):
        self.worker, self.engine, self.rules, self.seq = worker, engine, rules, 100
        self.start = datetime.now(UTC)

    def readings(self, readings=None, quality=None, age_s=2):
        self.seq += 1
        handle(self.worker, telemetry(age_s, readings, quality, seq=self.seq), TELEMETRY)

    def heartbeat(self, device="ZC1", at_s=0.0):
        """A DEVICE_STATUS from the device, stamped at_s seconds after the start."""
        self.seq += 1
        ts = self.start + timedelta(seconds=at_s)
        stamp = f"{ts:%Y-%m-%dT%H:%M:%S}.{ts.microsecond // 1000:03d}Z"
        handle(self.worker, heartbeat(device, ts=stamp, seq=self.seq), STATUS.format(device))

    def last_will(self, device="ZC1"):
        handle(self.worker, {"v": 1, "type": "DEVICE_STATUS", "device": device, "online": False},
               STATUS.format(device))

    def sweep(self, at_s: float):
        sweep(self.engine, ORG, SITE, self.rules, self.start + timedelta(seconds=at_s))

    def wait_out_delays(self, seconds: float):
        """As if every pending alert was first seen this long ago."""
        with self.engine.begin() as connection:
            connection.execute(update(Alert).where(Alert.raised_at.is_(None)).values(
                first_seen_at=datetime.now(UTC) - timedelta(seconds=seconds)))


@pytest.fixture
def farm(worker, seeded, rules):
    return Farm(worker, seeded, rules)


# --- SENSOR_FAULT: a reading with quality FAULT ----------------------------------------------


def test_a_faulty_probe_raises_sensor_fault_after_its_delay(api, farm):
    farm.readings({"ph": None}, {"ph": "FAULT"})
    assert alerts(api) == []  # alerts.json SENSOR_FAULT delayS 60: not yet
    farm.wait_out_delays(61)
    farm.readings({"ph": None}, {"ph": "FAULT"})
    found = only(api)
    assert (found["kind"], found["severity"], found["zone"]) == ("SENSOR_FAULT", "WARNING", "Z1")
    assert found["message"] == "The pH sensor in Zone 1 reports a fault: it gives no reading"
    assert found["systemAction"] == "Doses to Zone 1 are refused until it reads again"


def test_it_clears_when_the_probe_reads_again(api, farm):
    farm.readings({"ph": None}, {"ph": "FAULT"})
    farm.wait_out_delays(61)
    farm.readings({"ph": None}, {"ph": "FAULT"})
    farm.readings({"ph": None}, {"ph": "STALE"})  # stale says nothing about the fault
    assert only(api)["status"] == "OPEN"
    farm.readings({"ph": 6.1}, {"ph": "OK"})
    assert only(api, status="CLEARED")["kind"] == "SENSOR_FAULT"


def test_a_fault_that_ends_within_its_delay_raises_nothing(api, farm):
    farm.readings({"solTempC": None}, {"solTempC": "FAULT"})
    farm.readings({"solTempC": 23.0}, {"solTempC": "OK"})
    farm.wait_out_delays(61)
    farm.readings()
    assert alerts(api) == []


def test_the_controllers_own_event_and_the_reading_are_one_alert(api, farm, worker):
    """The same key (kind, zone, metric) as a SENSOR_FAULT event (§6.5). Once a reading judges it,
    it takes the newest reading's words, as every open reading alert does (#81, §11.2)."""
    handle(worker, event(kind="SENSOR_FAULT", severity="WARNING", message="pH probe not answering",
                         systemAction="Doses refused", nextStep="Check the probe",
                         detail={"metric": "ph"}), EVENTS)
    assert only(api)["message"] == "pH probe not answering"  # raised by the event, at once
    farm.readings({"ph": None}, {"ph": "FAULT"})
    found = only(api)  # still one alert
    assert (found["kind"], found["severity"], found["status"]) == ("SENSOR_FAULT", "WARNING", "OPEN")
    assert found["message"] == "The pH sensor in Zone 1 reports a fault: it gives no reading"
    farm.readings({"ph": 6.1}, {"ph": "OK"})
    assert only(api)["status"] == "CLEARED"


def test_a_probe_that_is_not_for_dosing_takes_no_action(api, farm):
    farm.readings({"reservoirPct": None}, {"reservoirPct": "FAULT"})
    farm.wait_out_delays(61)
    farm.readings({"reservoirPct": None}, {"reservoirPct": "FAULT"})
    found = only(api)
    assert found["message"] == "The reservoir level sensor in Zone 1 reports a fault: it gives no reading"
    assert found["systemAction"] == "No automatic action: the farm keeps running as it was"


# --- DEVICE_OFFLINE: a last will, or no message for thresholdS -------------------------------


def test_a_controller_silent_for_3_minutes_is_offline_and_back_when_heard(api, farm):
    farm.heartbeat(at_s=0)
    farm.sweep(at_s=170)
    assert alerts(api) == []
    farm.sweep(at_s=181)  # alerts.json DEVICE_OFFLINE thresholdS 180, delayS 0
    found = only(api)
    assert (found["kind"], found["severity"], found["zone"]) == ("DEVICE_OFFLINE", "CRITICAL", "Z1")
    assert found["message"] == "The Zone 1 controller (ZC1) is offline: it has sent nothing for 3 min"
    assert found["systemAction"] == "Doses to Zone 1 are refused, and its readings show as old, until it is back"
    farm.heartbeat(at_s=200)
    farm.sweep(at_s=201)
    assert only(api)["status"] == "CLEARED"


def test_a_last_will_is_offline_at_once(api, farm):
    farm.heartbeat(at_s=0)
    farm.last_will()
    farm.sweep(at_s=5)
    assert only(api)["message"] == "The Zone 1 controller (ZC1) is offline: it dropped off the network (its last will)"
    farm.heartbeat(at_s=10)  # anything new from it clears the last will
    farm.sweep(at_s=11)
    assert only(api)["status"] == "CLEARED"


def test_a_device_never_heard_from_is_not_offline(api, farm):
    """Not connected yet, or not fitted (client_B's climate controller): nothing to raise."""
    farm.sweep(at_s=10_000)
    assert alerts(api) == []


def test_a_silent_power_monitor_is_a_warning(api, farm):
    farm.heartbeat("PM1", at_s=0)
    farm.sweep(at_s=181)
    found = only(api)
    assert (found["severity"], found["zone"]) == ("WARNING", None)
    assert found["message"] == "The power monitor (PM1) is offline: it has sent nothing for 3 min"


def test_one_offline_device_is_one_alert_however_long_it_lasts(api, farm):
    farm.heartbeat(at_s=0)
    for at_s in (181, 191, 600):
        farm.sweep(at_s=at_s)
    assert len(alerts(api)) == 1


# --- READING_STALE: readings stop while the controller talks, or a reading marked STALE --------


def test_readings_that_stop_while_the_controller_is_online_are_stale(api, farm):
    farm.readings(age_s=0)  # the newest reading, now
    farm.heartbeat(at_s=125)  # the controller still talks
    farm.sweep(at_s=126)
    assert alerts(api) == []  # alerts.json READING_STALE thresholdS 120, delayS 60: not yet
    farm.wait_out_delays(61)
    farm.heartbeat(at_s=190)
    farm.sweep(at_s=191)
    found = only(api)
    assert (found["kind"], found["severity"], found["zone"]) == ("READING_STALE", "WARNING", "Z1")
    assert found["message"] == "No new readings from Zone 1 for over 2 min, though its controller is online"
    farm.readings(age_s=0)
    farm.sweep(at_s=1)  # judged by the newest reading's ts against the sweep's clock
    assert only(api)["status"] == "CLEARED"


def test_an_offline_controller_is_offline_not_stale(api, farm):
    """Offline says more: READING_STALE stands down while DEVICE_OFFLINE stands."""
    farm.readings(age_s=0)
    farm.heartbeat(at_s=0)
    farm.sweep(at_s=300)
    assert [a["kind"] for a in alerts(api)] == ["DEVICE_OFFLINE"]


def test_a_reading_the_controller_marks_stale_raises_and_clears(api, farm):
    farm.readings({"ec": 1.5}, {"ec": "STALE"})
    farm.wait_out_delays(61)
    farm.readings({"ec": 1.5}, {"ec": "STALE"})
    found = only(api)
    assert (found["kind"], found["message"]) == (
        "READING_STALE", "The EC reading in Zone 1 is stale: the sensor hasn't sent a new value")
    assert found["systemAction"] == "Doses to Zone 1 are refused until it is fresh again"
    farm.readings({"ec": 1.5}, {"ec": "OK"})
    assert only(api)["status"] == "CLEARED"


# --- Thresholds from the config pack ---------------------------------------------------------


def test_thresholds_come_from_alerts_json_or_else_the_contracts_own_times(pack):
    rules = AlertRules.from_pack(pack)
    assert (rules.device_offline_s, rules.reading_stale_s) == (180.0, 120.0)  # client_A's
    assert rules.delay("READING_STALE") == timedelta(seconds=60)
    alerts_json = json.loads((pack / "alerts.json").read_text(encoding="utf-8"))
    alerts_json["events"]["DEVICE_OFFLINE"]["thresholdS"] = 60
    del alerts_json["events"]["READING_STALE"]
    (pack / "alerts.json").write_text(json.dumps(alerts_json), encoding="utf-8")
    rules = AlertRules.from_pack(pack)
    assert (rules.device_offline_s, rules.reading_stale_s) == (60.0, FRESH_FOR_S)
    assert OFFLINE_AFTER_S == 180.0  # §14's 3 minutes when a pack gives none


def test_a_shorter_offline_threshold_from_the_pack_is_used(api, farm, pack, seeded):
    alerts_json = json.loads((pack / "alerts.json").read_text(encoding="utf-8"))
    alerts_json["events"]["DEVICE_OFFLINE"]["thresholdS"] = 60
    (pack / "alerts.json").write_text(json.dumps(alerts_json), encoding="utf-8")
    farm.rules = AlertRules.from_pack(pack)
    farm.heartbeat(at_s=0)
    farm.sweep(at_s=61)
    assert only(api)["message"] == "The Zone 1 controller (ZC1) is offline: it has sent nothing for 1 min"


def test_a_playground_reset_clears_them_too(api, farm, seeded):
    farm.heartbeat(at_s=0)
    farm.sweep(at_s=181)
    record_reset(seeded, SiteRef(ORG, SITE), datetime.now(UTC))
    assert only(api)["status"] == "CLEARED"  # (the sweep's clock ran ahead, so it is still listed)


# --- Just after the worker starts (review of #85) ---------------------------------------------


def raised_count(engine) -> int:
    with engine.connect() as connection:
        return connection.scalar(select(func.count()).select_from(Alert).where(Alert.raised_at.is_not(None)))


def test_just_after_a_start_the_sweep_only_clears(api, farm):
    farm.heartbeat(at_s=0)
    sweep(farm.engine, ORG, SITE, farm.rules, farm.start + timedelta(seconds=181), raising=False)
    assert alerts(api) == []  # silent for 3 min, but the worker may have been the one away
    farm.sweep(at_s=181)
    assert only(api)["status"] == "OPEN"
    farm.heartbeat(at_s=200)
    sweep(farm.engine, ORG, SITE, farm.rules, farm.start + timedelta(seconds=201), raising=False)
    assert only(api)["status"] == "CLEARED"  # clearing goes on as ever


def test_a_restarted_worker_waits_its_offline_time_before_raising(farm, seeded):
    """The worker was away for 10 minutes: every device's newest message is old. For the first
    3 minutes (thresholdS) the sweep raises nothing; a device still silent after that is offline."""
    farm.heartbeat(at_s=-600)  # heard 10 minutes before the worker started
    offsets = iter([0, 10, 170, 190])  # the start, then three sweeps, in seconds
    seen = []

    def clock():
        return farm.start + timedelta(seconds=next(offsets))

    async def sleep(_seconds):
        seen.append(raised_count(seeded))  # after the previous sweep (the first: none yet)
        if len(seen) == 4:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(watch(seeded, ORG, SITE, farm.rules, sleep=sleep, clock=clock))
    assert seen == [0, 0, 0, 1]  # nothing at 10 s or 170 s; DEVICE_OFFLINE at 190 s
