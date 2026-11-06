"""The simulator obeys DOSE, STOP and RESUME on a real broker (T4-8).

The devices and clock task run as sim.main runs them, at x60. A "cloud" client publishes commands
on the contract topics and listens to everything, as the back end will. Every message received is
checked by Track 1's watch tool: its models, and CMD_STATE's shape until that model exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import aiomqtt
import pytest
from app.contract.watch import check

from sim.clock import SimClock, TickLoop
from sim.config import load_devices, load_site, load_speed_options, read_pack_file
from sim.devices.readings import ReadingDevice
from sim.farm import load_farm
from sim.main import controllers_of, make_device

pytestmark = pytest.mark.broker
SPEED = 60


def stamp(moment):
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def command(kind, *, issued_ago_s=0.0, **body):
    issued = datetime.now(UTC) - timedelta(seconds=issued_ago_s)
    expiry = timedelta(minutes=5) if kind == "STOP" else timedelta(seconds=60)
    return {
        "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "ts": stamp(issued),
        "site": "siet-ph1", "device": "CLOUD", "cmdId": str(uuid.uuid4()), "kind": kind,
        "issuedAt": stamp(issued), "expiresAt": stamp(issued + expiry), "issuedBy": "user:42",
        **body,
    }


def dose(**lines):
    return command("DOSE", target={"zone": "Z1"},
                   lines=[{"line": line, "ml": ml} for line, ml in lines.items()],
                   order=list(lines), mixWaitS=120, maxDurationS=600, tolerancePct=5)


class Farm:
    """The simulator in-process, and a cloud client that sends and records."""

    def __init__(self, settings, tmp_path):
        self.settings, self.state_dir = settings, tmp_path
        self.received: list[dict] = []
        self.verdicts: list[tuple[str, str]] = []

    async def __aenter__(self):
        client_dir = self.settings.client_dir
        site = load_site(client_dir)
        self.cloud = aiomqtt.Client(self.settings.mqtt_host, self.settings.mqtt_port,
                                    identifier=f"test-cloud-{uuid.uuid4().hex[:8]}")
        await self.cloud.__aenter__()
        await self.cloud.subscribe(f"farm/{site}/#", qos=1)
        self.listener = asyncio.create_task(self._listen())
        clock = SimClock(datetime.now(UTC), SPEED, load_speed_options(client_dir))
        farm = load_farm(client_dir)
        farm.now = clock.last_tick
        alerts = read_pack_file(client_dir, "alerts.json")
        devices = [make_device(d, site, 31, farm, alerts, clock, client_dir=client_dir,
                               state_dir=self.state_dir) for d in load_devices(client_dir)]
        loop = TickLoop(clock, farm, [d for d in devices if isinstance(d, ReadingDevice)],
                        controllers=controllers_of(devices))
        self.tasks = [asyncio.create_task(d.run(self.settings)) for d in devices]
        self.tasks.append(asyncio.create_task(loop.run()))
        await self.wait_for(lambda m: m["type"] == "ZONE_STATE" and m["device"] == "ZC1")
        return self

    async def __aexit__(self, *exc):
        for task in [*self.tasks, self.listener]:
            task.cancel()
        await asyncio.gather(*self.tasks, self.listener, return_exceptions=True)
        await self.cloud.__aexit__(None, None, None)

    async def _listen(self):
        async for message in self.cloud.messages:
            if message.retain:
                continue  # left by earlier runs
            payload = json.loads(message.payload)
            if payload.get("bootId") == 31:
                self.received.append(payload)
                self.verdicts.append(check(message.payload))

    async def send(self, topic, cmd):
        await self.cloud.publish(topic, json.dumps(cmd), qos=1)

    async def wait_for(self, done, timeout_s=15.0):
        async with asyncio.timeout(timeout_s):
            while not any(done(m) for m in self.received):
                await asyncio.sleep(0.05)
        return [m for m in self.received if done(m)]

    def states(self, cmd):
        return [m for m in self.received
                if m["type"] == "CMD_STATE" and m["cmdId"] == cmd["cmdId"]]


ZONE_CMD = "farm/siet-ph1/zone/Z1/cmd"
STOP_TOPIC = "farm/siet-ph1/stop"


def test_a_dose_completes_and_ec_rises_in_a_later_reading(broker, run, tmp_path):
    async def scenario():
        async with Farm(broker, tmp_path) as farm:
            readings = await farm.wait_for(  # start-up waits for ZONE_STATE, not a reading
                lambda m: m["type"] == "ZONE_TELEMETRY" and m["zone"] == "Z1")
            ec_before = readings[-1]["readings"]["ec"]
            cmd = dose(A=120, B=120)
            await farm.send(ZONE_CMD, cmd)
            mixed = await farm.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"]
                                        and m.get("after") is not None)
            later = await farm.wait_for(
                lambda m: m["type"] == "ZONE_TELEMETRY" and m["zone"] == "Z1"
                and m["ts"] > mixed[0]["ts"])
            return farm, cmd, ec_before, mixed[0], later[0]

    farm, cmd, ec_before, mixed, later = run(scenario())
    states = [m["state"] for m in farm.states(cmd)]
    assert states[0] == "ACCEPTED" and "EXECUTING" in states
    assert states[-2:] == ["COMPLETED", "COMPLETED"]
    assert mixed["after"]["ec"] > mixed["before"]["ec"]
    assert later["readings"]["ec"] > ec_before
    assert all(verdict == "OK" for verdict, _ in farm.verdicts), farm.verdicts


def test_stop_mid_dose_ends_it_stopped_and_resume_clears_stopped(broker, run, tmp_path):
    async def scenario():
        async with Farm(broker, tmp_path) as farm:
            cmd = dose(A=300)  # 150 simulated seconds: 2.5 real seconds at x60
            await farm.send(ZONE_CMD, cmd)
            await farm.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"]
                                and m.get("state") == "EXECUTING")
            await asyncio.sleep(0.5)
            stop = command("STOP", target={"scope": "ZONE", "zone": "Z1"})
            await farm.send(STOP_TOPIC, stop)
            await farm.wait_for(lambda m: m.get("cmdId") == stop["cmdId"]
                                and m.get("state") == "COMPLETED")
            blocked = dose(A=10)
            await farm.send(ZONE_CMD, blocked)
            await farm.wait_for(lambda m: m.get("cmdId") == blocked["cmdId"])
            resume = command("RESUME", target={"zone": "Z1"})
            await farm.send(ZONE_CMD, resume)
            await farm.wait_for(lambda m: m.get("cmdId") == resume["cmdId"]
                                and m.get("state") == "COMPLETED")
            after = await farm.wait_for(lambda m: m["type"] == "ZONE_STATE"
                                        and m["stopped"] is False and m["ts"] >= resume["ts"])
            return farm, cmd, blocked, after

    farm, cmd, blocked, after = run(scenario())
    final = farm.states(cmd)[-1]
    assert (final["state"], final["reason"]) == ("STOPPED", "STOP_REQUESTED")
    assert 0 < final["lines"][0]["deliveredMl"] < 300
    assert [(m["state"], m["reason"]) for m in farm.states(blocked)] == [
        ("REJECTED", "ZONE_STOPPED")]
    assert after
    assert all(verdict == "OK" for verdict, _ in farm.verdicts), farm.verdicts


def test_expired_and_blocked_commands_are_rejected_with_their_reason(broker, run, tmp_path):
    async def scenario():
        async with Farm(broker, tmp_path) as farm:
            expired = dose(A=10)
            issued = datetime.now(UTC) - timedelta(seconds=120)
            expired.update(issuedAt=stamp(issued), ts=stamp(issued),
                           expiresAt=stamp(issued + timedelta(seconds=60)))
            too_much = dose(A=301)  # over limits.json maxPerDoseMl
            for cmd in (expired, too_much):
                await farm.send(ZONE_CMD, cmd)
            for cmd in (expired, too_much):
                await farm.wait_for(lambda m, c=cmd: m.get("cmdId") == c["cmdId"])
            return farm, expired, too_much

    farm, expired, too_much = run(scenario())
    assert [(m["state"], m["reason"]) for m in farm.states(expired)] == [
        ("REJECTED", "EXPIRED_ON_ARRIVAL")]
    assert [(m["state"], m["reason"]) for m in farm.states(too_much)] == [
        ("REJECTED", "LIMIT_DOSE")]
    assert all(verdict == "OK" for verdict, _ in farm.verdicts), farm.verdicts


def test_a_stop_for_another_zone_is_not_answered_by_zc1(broker, run, tmp_path):
    async def scenario():
        async with Farm(broker, tmp_path) as farm:
            stop = command("STOP", target={"scope": "ZONE", "zone": "Z2"})
            await farm.send(STOP_TOPIC, stop)
            answers = await farm.wait_for(lambda m: m.get("cmdId") == stop["cmdId"]
                                          and m.get("state") == "COMPLETED")
            with contextlib.suppress(TimeoutError):
                await farm.wait_for(lambda m: m.get("cmdId") == stop["cmdId"]
                                    and m["device"] == "ZC1", timeout_s=1.0)
            return farm, stop, answers

    farm, stop, answers = run(scenario())
    assert {m["device"] for m in farm.states(stop)} == {"ZC2"}
    assert answers
