"""The playground's control API drives the running simulator (T4-9), on a real broker.

The devices, the clock task, the playground and its control API run as sim.main runs them, at
x60. The test asks the control API over HTTP, as the back end will, sends commands as the cloud,
and checks every message it receives with Track 1's watch tool.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import aiomqtt
import pytest
from app.contract.watch import check

from sim.clock import SimClock, TickLoop
from sim.config import (
    load_devices,
    load_min_real_reading_interval_s,
    load_site,
    load_speed_options,
    read_pack_file,
)
from sim.control_api import serve
from sim.devices.readings import ReadingDevice
from sim.farm import load_farm
from sim.main import controllers_of, make_device
from sim.playground import Playground

pytestmark = pytest.mark.broker
SPEED = 60
BOOT_ID = 32
ZONE_CMD = "farm/siet-ph1/zone/Z1/cmd"


def stamp(moment):
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def command(kind, **body):
    now = datetime.now(UTC)
    return {
        "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "ts": stamp(now), "site": "siet-ph1",
        "device": "CLOUD", "cmdId": str(uuid.uuid4()), "kind": kind, "issuedAt": stamp(now),
        "expiresAt": stamp(now + timedelta(seconds=60)), "issuedBy": "user:42",
        "target": {"zone": "Z1"}, **body,
    }


def dose(**lines):
    return command("DOSE", lines=[{"line": k, "ml": v} for k, v in lines.items()],
                   order=list(lines), mixWaitS=60, maxDurationS=600, tolerancePct=5)


class Sim:
    """The simulator in-process with its control API, and a cloud client that records."""

    def __init__(self, settings, tmp_path):
        self.settings, self.state_dir = settings, tmp_path
        self.received: list[dict] = []
        self.verdicts: list[tuple[str, str]] = []
        self.wills: list[tuple[str, dict]] = []  # last wills: they carry no bootId (6.4)

    async def __aenter__(self):
        client_dir = self.settings.client_dir
        site = load_site(client_dir)
        self.cloud = aiomqtt.Client(self.settings.mqtt_host, self.settings.mqtt_port,
                                    identifier=f"test-cloud-{uuid.uuid4().hex[:8]}")
        await self.cloud.__aenter__()
        await self.cloud.subscribe(f"farm/{site}/#", qos=1)
        self.listener = asyncio.create_task(self._listen())
        self.clock = SimClock(datetime.now(UTC), SPEED, load_speed_options(client_dir))
        farm = load_farm(client_dir)
        farm.now = self.clock.last_tick
        alerts = read_pack_file(client_dir, "alerts.json")
        devices = [make_device(d, site, BOOT_ID, farm, alerts, self.clock, client_dir=client_dir,
                               state_dir=self.state_dir) for d in load_devices(client_dir)]
        min_real_s = load_min_real_reading_interval_s(client_dir)
        self.playground = Playground(farm, self.clock, client_dir,
                                     controllers=controllers_of(devices), devices=devices,
                                     min_real_reading_s=min_real_s,
                                     next_boot_id=lambda _: BOOT_ID + 1)
        loop = TickLoop(self.clock, self.playground,
                        [d for d in devices if isinstance(d, ReadingDevice)],
                        min_real_reading_s=min_real_s, controllers=controllers_of(devices))
        self.server = await serve(self.playground, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self.tasks = [asyncio.create_task(d.run(self.settings)) for d in devices]
        self.tasks.append(asyncio.create_task(loop.run()))
        await self.next_reading()
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        for task in [*self.tasks, self.listener]:
            task.cancel()
        await asyncio.gather(*self.tasks, self.listener, return_exceptions=True)
        await self.cloud.__aexit__(None, None, None)

    async def _listen(self):
        async for message in self.cloud.messages:
            if message.retain:
                continue  # left by earlier runs
            payload = json.loads(message.payload)
            if payload.get("type") == "DEVICE_STATUS" and "bootId" not in payload:
                self.wills.append((str(message.topic), payload))
                self.verdicts.append(check(message.payload))
            elif payload.get("bootId") in (BOOT_ID, BOOT_ID + 1):  # + 1: after a drill's reboot
                self.received.append(payload)
                self.verdicts.append(check(message.payload))

    async def api(self, method, path, body=None):
        """(status, JSON) from the control API, as the back end will ask it."""
        data = b"" if body is None else json.dumps(body).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(f"{method} {path} HTTP/1.1\r\nHost: simulator\r\n"
                     f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        await writer.drain()
        head, _, payload = (await reader.read()).partition(b"\r\n\r\n")
        writer.close()
        return int(head.split()[1]), json.loads(payload)

    async def wait_for(self, done, timeout_s=15.0):
        async with asyncio.timeout(timeout_s):
            while not (found := [m for m in self.received if done(m)]):
                await asyncio.sleep(0.05)
        return found

    async def next_reading(self, after=None, zone="Z1"):
        """The first ZONE_TELEMETRY for the zone taken after simulated time `after` (an API
        answer's simTime), so a reading already on its way can't be mistaken for it."""
        found = await self.wait_for(
            lambda m: m["type"] == "ZONE_TELEMETRY" and m["zone"] == zone
            and (after is None or m["simTs"] > after)
        )
        return found[0]


def all_ok(sim):
    assert sim.verdicts and all(verdict == "OK" for verdict, _ in sim.verdicts), sim.verdicts


def test_a_low_reservoir_held_shows_in_the_next_reading_and_blocks_a_dose(broker, run, tmp_path):
    async def scenario():
        async with Sim(broker, tmp_path) as sim:
            held = await sim.api("POST", "/holds",
                                 {"target": "Z1", "metric": "reservoirPct", "value": 15})
            reading = await sim.next_reading(held[1]["simTime"])
            cmd = dose(A=120, B=120)
            await sim.cloud.publish(ZONE_CMD, json.dumps(cmd), qos=1)
            answer = await sim.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"])
            out_of_range = await sim.api("POST", "/holds",
                                         {"target": "Z1", "metric": "reservoirPct", "value": 101})
            return sim, held, reading, answer, out_of_range

    sim, held, reading, answer, out_of_range = run(scenario())
    assert held[0] == 200 and held[1]["holds"][0]["value"] == 15
    assert reading["readings"]["reservoirPct"] == 15
    assert [(m["state"], m["reason"]) for m in answer] == [("REJECTED", "RESERVOIR_LOW")]
    assert out_of_range[0] == 422
    assert out_of_range[1]["error"]["details"]["fields"][0]["field"] == "body.value"
    all_ok(sim)


def test_sensor_offline_on_ph_reads_null_with_fault_and_fix_restores_it(broker, run, tmp_path):
    async def scenario():
        async with Sim(broker, tmp_path) as sim:
            drill = {"kind": "SENSOR_OFFLINE", "target": "Z1.ph"}
            triggered = await sim.api("POST", "/faults", {**drill, "on": True})
            offline = await sim.next_reading(triggered[1]["simTime"])
            fixed = await sim.api("POST", "/faults", {**drill, "on": False})
            back = await sim.next_reading(fixed[1]["simTime"])
            return sim, triggered, offline, fixed, back

    sim, triggered, offline, fixed, back = run(scenario())
    assert triggered[1]["faults"] == [{"kind": "SENSOR_OFFLINE", "target": "Z1.ph"}]
    assert (offline["readings"]["ph"], offline["quality"]["ph"]) == (None, "FAULT")
    assert fixed[1]["faults"] == []
    assert isinstance(back["readings"]["ph"], float) and back["quality"]["ph"] == "OK"
    all_ok(sim)


def test_pause_holds_the_clock_and_reset_returns_to_the_demo_start(broker, run, tmp_path):
    async def scenario():
        async with Sim(broker, tmp_path) as sim:
            paused = await sim.api("POST", "/speed", {"x": 0})
            await asyncio.sleep(1.5)
            still = await sim.api("GET", "/state")
            running = await sim.api("POST", "/speed", {"x": 60})
            await asyncio.sleep(1.0)
            moved = await sim.api("GET", "/state")
            hold = await sim.api("POST", "/holds",
                                 {"target": "Z1", "metric": "ec", "value": 2.0, "gradual": False})
            held = await sim.next_reading(hold[1]["simTime"])
            reset = await sim.api("POST", "/reset")
            after = await sim.next_reading(reset[1]["simTime"])
            return sim, paused, still, running, moved, held, reset, after

    sim, paused, still, running, moved, held, reset, after = run(scenario())
    assert paused[1]["speed"] == 0 and still[1]["simTime"] == paused[1]["simTime"]
    assert running[1]["speed"] == 60 and moved[1]["simTime"] > running[1]["simTime"]
    assert held["readings"]["ec"] == 2.0
    assert (reset[0], reset[1]["holds"], reset[1]["faults"]) == (200, [], [])
    assert (after["readings"]["ec"], after["readings"]["reservoirPct"]) == (1.30, 78)
    all_ok(sim)


def test_estop_during_a_dose_and_resume_only_after_release(broker, run, tmp_path):
    """T4-10: the E-STOP drill, then the person's RESUME."""
    estop = {"kind": "ESTOP", "target": "Z1"}

    async def scenario():
        async with Sim(broker, tmp_path) as sim:
            def answered(cmd, state=None):
                return sim.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"]
                                    and (state is None or m["state"] == state))

            cmd = dose(A=300)  # 150 simulated seconds: 2.5 real seconds at x60
            await sim.cloud.publish(ZONE_CMD, json.dumps(cmd), qos=1)
            await answered(cmd, "EXECUTING")
            await asyncio.sleep(0.5)
            pressed = await sim.api("POST", "/faults", {**estop, "on": True})
            await answered(cmd, "STOPPED")
            early = command("RESUME")
            await sim.cloud.publish(ZONE_CMD, json.dumps(early), qos=1)
            await answered(early)
            released = await sim.api("POST", "/faults", {**estop, "on": False})
            await sim.wait_for(lambda m: m.get("kind") == "ESTOP_RELEASED")
            resume = command("RESUME")
            await sim.cloud.publish(ZONE_CMD, json.dumps(resume), qos=1)
            await answered(resume, "COMPLETED")
            await sim.wait_for(lambda m: m["type"] == "ZONE_STATE" and m["zone"] == "Z1"
                               and m["stopped"] is False and m["ts"] >= resume["ts"])
            return sim, cmd, early, pressed, released

    sim, cmd, early, pressed, released = run(scenario())

    def states(c):
        return [(m["state"], m.get("reason")) for m in sim.received
                if m["type"] == "CMD_STATE" and m["cmdId"] == c["cmdId"]]

    final = [m for m in sim.received if m.get("cmdId") == cmd["cmdId"]][-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "ESTOP_DURING")
    assert 0 < final["lines"][0]["deliveredMl"] < 300
    assert states(early) == [("REJECTED", "ESTOP")]
    assert pressed[1]["faults"] == [estop] and released[1]["faults"] == []
    z1 = [m for m in sim.received if m.get("zone") == "Z1"]
    assert [m["kind"] for m in z1 if m["type"] == "EVENT"] == ["ESTOP_PRESSED", "ESTOP_RELEASED"]
    assert any(m["type"] == "ZONE_STATE" and m["estop"] and m["stopped"] for m in z1)
    all_ok(sim)


def test_a_stuck_probe_reads_stale_and_a_dose_is_rejected(broker, run, tmp_path):
    """T4-11: STUCK_READING on EC."""

    async def scenario():
        async with Sim(broker, tmp_path) as sim:
            drill = {"kind": "STUCK_READING", "target": "Z1.ec"}
            triggered = await sim.api("POST", "/faults", {**drill, "on": True})
            reading = await sim.next_reading(triggered[1]["simTime"])
            cmd = dose(A=120, B=120)
            await sim.cloud.publish(ZONE_CMD, json.dumps(cmd), qos=1)
            answer = await sim.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"])
            fixed = await sim.api("POST", "/faults", {**drill, "on": False})
            back = await sim.next_reading(fixed[1]["simTime"])
            return sim, reading, answer, back

    sim, reading, answer, back = run(scenario())
    assert (reading["readings"]["ec"], reading["quality"]["ec"]) == (1.30, "STALE")
    assert [(m["state"], m["reason"]) for m in answer] == [("REJECTED", "SENSOR_STALE")]
    assert back["quality"]["ec"] == "OK"
    all_ok(sim)


def test_a_controller_lost_mid_dose_comes_back_stopped_with_outputs_off(broker, run, tmp_path):
    """T4-11: DEVICE_OFFLINE on ZC1 while it doses. Its last will goes out, a dose sent while
    it is away waits at the broker, and it comes back as a new boot, stopped."""
    offline = {"kind": "DEVICE_OFFLINE", "target": "ZC1"}

    async def scenario():
        async with Sim(broker, tmp_path) as sim:
            def answered(cmd, state=None):
                return sim.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"]
                                    and (state is None or m["state"] == state))

            cmd = dose(A=300)
            await sim.cloud.publish(ZONE_CMD, json.dumps(cmd), qos=1)
            await answered(cmd, "EXECUTING")
            await asyncio.sleep(0.5)
            await sim.api("POST", "/faults", {**offline, "on": True})
            async with asyncio.timeout(10):
                while not sim.wills:
                    await asyncio.sleep(0.05)
            held = dose(A=20)  # sent while ZC1 is away: the broker keeps it
            await sim.cloud.publish(ZONE_CMD, json.dumps(held), qos=1)
            await asyncio.sleep(1.5)
            unanswered = [m for m in sim.received if m.get("cmdId") == held["cmdId"]]
            await sim.api("POST", "/faults", {**offline, "on": False})
            await answered(cmd, "PARTIAL")
            await answered(held)
            resume = command("RESUME")
            await sim.cloud.publish(ZONE_CMD, json.dumps(resume), qos=1)
            await answered(resume, "COMPLETED")
            return sim, cmd, held, unanswered

    sim, cmd, held, unanswered = run(scenario())
    assert sim.wills == [("farm/siet-ph1/device/ZC1/status",
                          {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False})]
    assert unanswered == []  # nothing answers while the controller is away
    zc1 = [m for m in sim.received if m["device"] == "ZC1"]
    new_boot = [m for m in zc1 if m["bootId"] == BOOT_ID + 1]
    assert (new_boot[0]["type"], new_boot[0]["seq"], new_boot[0]["online"]) == (
        "DEVICE_STATUS", 1, True)
    closed = [m for m in new_boot if m.get("cmdId") == cmd["cmdId"]][-1]
    assert (closed["state"], closed["reason"]) == ("PARTIAL", "REBOOT_MID_DOSE")
    assert 0 < closed["lines"][0]["deliveredMl"] < 300
    assert [(m["state"], m["reason"]) for m in new_boot if m.get("cmdId") == held["cmdId"]] == [
        ("REJECTED", "ZONE_STOPPED")]
    assert [m["detail"] for m in new_boot if m["type"] == "EVENT"] == [{"reason": "REBOOT"}]
    back = next(m for m in new_boot if m["type"] == "ZONE_STATE")
    assert back["stopped"] is True and back["operation"] is None
    assert back["outputs"] == {"Z1-CIRC": "ON", "Z1-REFILL": "CLOSED", "Z1-DOSE-A": "OFF",
                               "Z1-DOSE-B": "OFF", "Z1-DOSE-ACID": "OFF"}
    all_ok(sim)
