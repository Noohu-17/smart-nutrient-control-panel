"""The DEVICE_OFFLINE drill (T4-11): a zone controller goes silent, then comes back as a new boot,
stopped, with its outputs off and an interrupted dose closed with what was measured.

The real device, farm, controller and playground, with the clock task driven by hand. That the
broker publishes the last will is tested on a real broker (tests/integration).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.contract.watch import check

from sim.clock import SimClock, TickLoop
from sim.config import read_pack_file
from sim.farm import load_farm
from sim.main import controllers_of, make_device
from sim.playground import Playground, PlaygroundError
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)
OFFLINE = {"kind": "DEVICE_OFFLINE", "target": "ZC1"}


class Rig:
    def __init__(self, tmp_path):
        self.real_s = 1000.0
        self.clock = SimClock(SIM_START, 60, (0, 1, 60, 288), monotonic=lambda: self.real_s)
        self.farm = load_farm(CLIENT_A)
        self.farm.now = SIM_START
        alerts = read_pack_file(CLIENT_A, "alerts.json")
        entry = {"id": "ZC1", "kind": "ZONE_CONTROLLER", "zone": "Z1"}
        self.device = make_device(entry, "siet-ph1", 3, self.farm, alerts, self.clock,
                                  client_dir=CLIENT_A, state_dir=tmp_path)
        self.zone = self.device.zone
        self.boots = iter(range(4, 100))
        self.playground = Playground(
            self.farm, self.clock, CLIENT_A, controllers=controllers_of([self.device]),
            devices=[self.device], min_real_reading_s=5, next_boot_id=lambda _: next(self.boots),
        )
        self.loop = TickLoop(self.clock, self.playground, [self.device], min_real_reading_s=5,
                             controllers=controllers_of([self.device]))
        self.loop.tick()
        self.drain()

    def run(self, real_s, tick_s=0.1):
        for _ in range(round(real_s / tick_s)):
            self.real_s += tick_s
            self.loop.tick()

    def drain(self):
        """What the device has queued, in order, with its envelope."""
        messages = [item.build() for item in self.device._outbox]
        self.device._outbox.clear()
        return messages

    def send(self, cmd):
        self.device.on_message(self.device.cmd_topic, json.dumps(cmd))
        return cmd

    def drill(self, on):
        self.playground.fault({**OFFLINE, "on": on})


def command(kind, issued_ago_s=0, **body):
    issued = datetime.now(UTC) - timedelta(seconds=issued_ago_s)
    stamp = lambda t: f"{t:%Y-%m-%dT%H:%M:%S}.{t.microsecond // 1000:03d}Z"  # noqa: E731
    return {
        "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "ts": stamp(issued),
        "site": "siet-ph1", "device": "CLOUD", "cmdId": str(uuid.uuid4()), "kind": kind,
        "issuedAt": stamp(issued), "expiresAt": stamp(issued + timedelta(seconds=60)),
        "issuedBy": "user:42", "target": {"zone": "Z1"}, **body,
    }


def dose(issued_ago_s=0, **lines):
    return command("DOSE", issued_ago_s, lines=[{"line": k, "ml": v} for k, v in lines.items()],
                   order=list(lines), mixWaitS=60, maxDurationS=600, tolerancePct=5)


def of(messages, msg_type):
    return [m for m in messages if m["type"] == msg_type]


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_offline_mid_dose_the_pumps_stop_and_the_device_says_nothing(rig):
    rig.send(dose(A=300))
    rig.run(1.0)  # 60 simulated seconds at x60: about 120 mL
    rig.drain()

    rig.drill(True)

    assert rig.device.powered is False
    assert rig.zone.outputs == {"Z1-CIRC": "OFF", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF",
                                "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}
    tank_l = rig.zone.day_tank_l["A"]
    rig.run(10.0)  # 10 minutes: two readings would have been due
    assert rig.drain() == []
    assert rig.zone.day_tank_l["A"] == tank_l
    assert rig.playground.state()["faults"] == [OFFLINE]


def test_it_comes_back_as_a_new_boot_stopped_with_outputs_off_and_the_dose_closed(rig):
    cmd = rig.send(dose(A=300))
    rig.run(1.0)
    rig.drill(True)
    rig.run(2.0)

    rig.drill(False)
    messages = rig.drain()

    first = messages[0]
    assert (first["type"], first["bootId"], first["seq"], first["online"]) == (
        "DEVICE_STATUS", 4, 1, True)
    assert all(m["bootId"] == 4 for m in messages)
    [closed] = [m for m in of(messages, "CMD_STATE") if m["cmdId"] == cmd["cmdId"]]
    assert (closed["state"], closed["reason"]) == ("PARTIAL", "REBOOT_MID_DOSE")
    [line] = closed["lines"]
    assert line["state"] == "SHORT" and 100 <= line["deliveredMl"] < 300
    [rebooted] = of(messages, "EVENT")
    assert (rebooted["kind"], rebooted["detail"]) == ("REBOOTED", {"reason": "REBOOT"})
    state = of(messages, "ZONE_STATE")[-1]
    assert (state["stopped"], state["operation"]) == (True, None)
    assert state["outputs"] == {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF",
                                "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}
    assert all(check(json.dumps(m))[0] == "OK" for m in messages), [
        check(json.dumps(m)) for m in messages]
    assert rig.playground.state()["faults"] == []

    resume = rig.send(command("RESUME"))
    assert [m["state"] for m in of(rig.drain(), "CMD_STATE")
            if m["cmdId"] == resume["cmdId"]] == ["ACCEPTED", "COMPLETED"]
    again = rig.send(dose(A=20))
    assert of(rig.drain(), "CMD_STATE")[0]["cmdId"] == again["cmdId"]


def test_what_went_in_before_the_drop_counts_towards_the_24h_totals(rig):
    rig.send(dose(A=300))
    rig.run(1.0)
    rig.drill(True)
    rig.drill(False)
    [closed] = [m for m in of(rig.drain(), "CMD_STATE") if m["state"] == "PARTIAL"]
    delivered = closed["lines"][0]["deliveredMl"]
    items = rig.device.controller.hal.nvs_read("doseHistory")["items"]
    assert [(line, ml) for _, line, ml in items] == [("A", delivered)]  # 9 rule 8


@pytest.mark.parametrize(
    ("issued_ago_s", "reason"), [(0, "ZONE_STOPPED"), (120, "EXPIRED_ON_ARRIVAL")]
)
def test_a_dose_held_by_the_broker_is_rejected_when_the_controller_returns(
    rig, issued_ago_s, reason
):
    rig.drill(True)
    held = dose(issued_ago_s, A=20)  # sent while offline: the broker keeps it (QoS 1 session)
    rig.drill(False)
    rig.drain()
    rig.send(held)  # delivered as the device reconnects
    assert [(m["state"], m["reason"]) for m in of(rig.drain(), "CMD_STATE")] == [
        ("REJECTED", reason)]


def test_fix_on_a_running_device_changes_nothing(rig):
    rig.drill(False)
    assert rig.device.powered and rig.device.envelope.boot_id == 3
    assert rig.drain() == []


@pytest.mark.parametrize("target", ["ZC9", "Z1"])
def test_only_a_device_of_this_run_can_be_switched_off(rig, target):
    with pytest.raises(PlaygroundError) as caught:
        rig.playground.fault({"kind": "DEVICE_OFFLINE", "target": target, "on": True})
    assert (caught.value.status, caught.value.field) == (422, "body.target")


def test_reset_brings_an_offline_controller_back_and_not_stopped(rig):
    rig.drill(True)
    rig.playground.reset()
    assert rig.device.powered and rig.device.envelope.boot_id == 4
    state = of(rig.drain(), "ZONE_STATE")[-1]
    assert state["stopped"] is False
    assert rig.playground.state()["faults"] == []
