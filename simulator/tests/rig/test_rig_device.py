"""ZC1 as the zone controller service plays it, fed the recorded gateway session (T1-32).

The device, its controller and the link are built as the service builds them, with clocks moved
by hand; every message ZC1 queues is checked against the contract models (Track 1's watch tool).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.contract.watch import check
from rig_support import T0, Clock, recording, zone_doc

from sim.controller.zone import ZoneController
from sim.messages import utc_ts
from sim.rig.config import RigSettings, load_rig_zones
from sim.rig.device import RigZoneDevice
from sim.rig.hal import RigHal
from sim.rig.link import RigLink

BASE = "m/dev/c/telemetry"


class Service:
    def __init__(self, tmp_path, pack, clock):
        self.clock = clock
        [zone] = load_rig_zones(pack)
        settings = RigSettings(BASE, "m/dev/c/relay-cmd", "RIGLINK", 90.0)
        self.link = RigLink(settings)
        self.boots = iter(range(2, 100))
        self.hal = RigHal(zone, tmp_path / "nvs_ZC1.json", self.link.send, utc_now=clock.utc,
                          monotonic=clock.mono)
        self.device = RigZoneDevice(zone, "siet-ph1", 1, self.hal, offline_after_s=90.0,
                                    next_boot_id=lambda: next(self.boots), resume_grace_s=10.0,
                                    now=clock.utc, monotonic=clock.mono)
        config = {"configVersion": None, "limits": zone.limits, "dose": zone.dose}
        self.device.controller = ZoneController(
            self.hal, zone.zone_id, zone.lines, config, {}, self.device.report,
            device_id="ZC1", zone_name=zone.name, unmonitored_tanks=zone.lines,
            refill_available=False)
        self.device.controller.start()
        self.link.devices.append(self.device)

    def rig_says(self, subtopic, payload, *, retained=False):
        self.link.on_message(f"{BASE}/{subtopic}", json.dumps(payload), retained=retained)

    def replay(self, rows):
        for row in rows:
            self.rig_says(row["subtopic"], row["payload"], retained=row["retained"])

    def drain(self):
        """What ZC1 queued, in order, with its envelope: (topic, message)."""
        items = [(item.topic, item.build()) for item in self.device._outbox]
        self.device._outbox.clear()
        return items

    def tick(self, seconds=0.5):
        self.clock.advance(seconds)
        self.device.tick()

    def command(self, kind, **body):
        expires = self.clock.utc() + timedelta(seconds=60)
        cmd = {"v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "cmdId": str(uuid.uuid4()),
               "kind": kind, "site": "siet-ph1", "device": "CLOUD",
               "expiresAt": f"{expires:%Y-%m-%dT%H:%M:%S}.000Z", "target": {"zone": "Z1"}, **body}
        self.device.on_message(self.device.cmd_topic, json.dumps(cmd))
        return cmd["cmdId"]


@pytest.fixture
def service(tmp_path, rig_pack):
    return Service(tmp_path, rig_pack, Clock(T0 + 1))


def of(messages, msg_type):
    return [m for _, m in messages if m["type"] == msg_type]


def test_retained_messages_alone_do_not_bring_zc1_online(service):
    service.replay([r for r in recording() if r["retained"]])
    assert service.device.online is False and service.drain() == []
    assert service.hal.latest["seq"] == 40  # but what they say is kept


def test_the_live_session_becomes_contract_messages(service):
    service.replay(recording())
    messages = service.drain()
    assert all(check(json.dumps(m))[0] == "OK" for _, m in messages), [
        check(json.dumps(m)) for _, m in messages]
    status = messages[0][1]
    assert (status["type"], status["online"], status["fw"], status["fwHash"]) == (
        "DEVICE_STATUS", True, "1.0.0", "a1b2c3d4")
    assert (status["rssiDbm"], status["freeHeapKb"], status["bootId"]) == (-75, 146, 1)
    telemetry = of(messages, "ZONE_TELEMETRY")
    # the retained update, now stale, then zone update 42 (once: its repeat is dropped), then 43
    assert [t["quality"]["ec"] for t in telemetry] == ["STALE", "OK", "STALE"]
    assert telemetry[1]["readings"]["ec"] == 1.3 and telemetry[1]["readings"]["ph"] == 6.1
    assert (telemetry[2]["readings"]["ph"], telemetry[2]["quality"]["ph"]) == (None, "FAULT")
    assert telemetry[1]["ts"] == utc_ts(datetime.fromtimestamp(T0 + 1, UTC))  # the rig's time
    assert {t["zone"] for t in telemetry} == {"Z1"}  # zone 2 is not run here
    state = of(messages, "ZONE_STATE")[-1]
    assert state["outputs"] == {"Z1-DOSE-A": "OFF", "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF",
                                "Z1-REFILL": "CLOSED"}
    assert {topic for topic, m in messages if m["type"] == "ZONE_TELEMETRY"} == {
        "farm/siet-ph1/zone/Z1/telemetry"}


def test_rig_silence_takes_zc1_offline_and_it_comes_back_as_the_same_boot(service):
    service.replay(recording())
    service.drain()
    service.tick(91)  # 3 missed heartbeats
    assert service.device.online is False
    assert not service.device._power_on.is_set()  # its link drops: the broker sends the will
    service.rig_says("sf/gateway/heartbeat", {**recording()[-1]["payload"], "seq": 200})
    assert service.device.online
    status = service.drain()[0][1]
    assert (status["type"], status["online"], status["bootId"]) == ("DEVICE_STATUS", True, 1)


def test_the_gateways_last_will_takes_zc1_offline_at_once(service):
    service.replay(recording())
    service.rig_says("sf/gateway/status", [{"bn": "gateway-01:", "n": "status", "vs": "offline"}])
    assert service.device.online is False


def test_a_gateway_restart_is_a_new_boot_for_zc1_stopped(service):
    service.replay(recording())
    service.drain()
    service.rig_says("sf/gateway/heartbeat", {**recording()[-1]["payload"], "boot": 8, "seq": 1})
    messages = service.drain()
    assert messages[0][1]["type"] == "DEVICE_STATUS" and messages[0][1]["bootId"] == 2
    assert all(m["bootId"] == 2 for _, m in messages)
    assert [e["kind"] for e in of(messages, "EVENT")] == ["REBOOTED"]
    assert of(messages, "ZONE_STATE")[-1]["stopped"] is True


def test_a_dose_through_the_link_finishes_with_the_rigs_measurement(service):
    service.replay(recording())
    service.tick(11)  # past the grace after the rig came back
    service.rig_says("sf/zone/1", zone_doc(ts=int(service.clock.unix), seq=44))  # pH back
    service.drain()
    cmd_id = service.command("DOSE", lines=[{"line": "B", "ml": 40}], order=["B"], mixWaitS=0,
                             maxDurationS=600, tolerancePct=5)
    [sent] = list(service.link._outbox)
    assert (sent["cmd"], sent["target"], sent["args"]["relay"], sent["args"]["seconds"]) == (
        "dose", "zone:1", "relay2", 20.0)
    report = {"schema": "sf/dose/1", "gateway": "gateway-01", "boot": 7, "seq": 5, "zone": 1,
              "device": "actuator-node-01", "dose_id": sent["args"]["dose_id"],
              "relay": "relay2", "state": "running", "reason": None, "duration_s": 20.0,
              "elapsed_s": 0, "ts": int(service.clock.unix)}
    service.rig_says("sf/dose", report)
    service.rig_says("sf/dose", {**report, "seq": 6, "state": "done", "elapsed_s": 20.0})
    service.rig_says("sf/dose", {**report, "seq": 6, "state": "done", "elapsed_s": 20.0})  # again
    service.tick()
    service.tick()
    states = [m for m in of(service.drain(), "CMD_STATE") if m["cmdId"] == cmd_id]
    assert [s["state"] for s in states] == ["ACCEPTED", "EXECUTING", "COMPLETED", "COMPLETED"]
    assert states[-1]["lines"][0]["deliveredMl"] == 40 and states[-1]["after"] is not None
    assert all(check(json.dumps(s))[0] == "OK" for s in states)


def test_a_gateway_restart_mid_dose_stops_the_box_and_reports_what_it_ran(service):
    """The gateway restarts while Zone 1's actuator box is dosing: ZC1's new boot switches the
    pump off, and the interrupted dose closes REBOOT_MID_DOSE with the box's measurement."""
    service.replay(recording())
    service.tick(11)
    service.rig_says("sf/zone/1", zone_doc(ts=int(service.clock.unix), seq=44))
    service.drain()
    cmd_id = service.command("DOSE", lines=[{"line": "B", "ml": 40}], order=["B"], mixWaitS=0,
                             maxDurationS=600, tolerancePct=5)
    [sent] = list(service.link._outbox)
    service.link._outbox.clear()
    report = {"schema": "sf/dose/1", "gateway": "gateway-01", "boot": 7, "seq": 5, "zone": 1,
              "device": "actuator-node-01", "dose_id": sent["args"]["dose_id"],
              "relay": "relay2", "state": "running", "reason": None, "duration_s": 20.0,
              "elapsed_s": 0, "ts": int(service.clock.unix)}
    service.rig_says("sf/dose", report)
    service.rig_says("sf/gateway/heartbeat", {**recording()[-1]["payload"], "boot": 8, "seq": 1})
    [off] = list(service.link._outbox)
    assert (off["cmd"], off["target"], off["args"]) == (
        "switch", "zone:1", {"relays": {"relay2": False}})
    service.rig_says("sf/dose", {**report, "boot": 8, "seq": 2, "state": "stopped",
                                 "reason": "command", "elapsed_s": 7.5})
    service.tick(11)  # past the grace after the restart
    states = [m for m in of(service.drain(), "CMD_STATE") if m["cmdId"] == cmd_id]
    assert (states[-1]["state"], states[-1]["reason"]) == ("PARTIAL", "REBOOT_MID_DOSE")
    assert (states[-1]["lines"][0]["deliveredMl"], states[-1]["bootId"]) == (15, 2)
    assert all(check(json.dumps(s))[0] == "OK" for s in states)


def test_another_gateways_messages_are_ignored(service):
    other = [{**r, "payload": {**r["payload"], "gateway": "gateway-09"}}
             for r in recording() if isinstance(r["payload"], dict)]
    service.replay(other)
    assert service.device.online is False and service.hal.latest is None


def test_a_message_that_is_not_json_is_dropped(service):
    service.link.on_message(f"{BASE}/sf/zone/1", b"{not json", retained=False)
    service.device.on_message(service.device.cmd_topic, b"not json")
    assert service.drain() == [] and service.hal.latest is None
