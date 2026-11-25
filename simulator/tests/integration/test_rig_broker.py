"""The zone controller service and a fake rig on a real broker (T1-32): the task's done-when.

The service runs as `python -m sim.rig` builds it, for ZC1 on the rig; the fake rig publishes sf/*
as gateway 1.0.0 does and obeys its commands, 20 times faster than real time. A "cloud" client
sends contract commands and checks everything ZC1 says with Track 1's watch tool.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiomqtt
import pytest
from app.contract.watch import check

from sim.rig.config import RigSettings
from sim.rig.fake import FakeRig
from sim.rig.service import build, tick_forever

sys.path.insert(0, str(Path(__file__).parents[1] / "rig"))
from rig_support import make_rig_pack  # noqa: E402

pytestmark = pytest.mark.broker
ZONE_CMD = "farm/siet-ph1/zone/Z1/cmd"
STOP_TOPIC = "farm/siet-ph1/stop"


def stamp(moment):
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond // 1000:03d}Z"


def command(kind, **body):
    now = datetime.now(UTC)
    return {"v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "ts": stamp(now),
            "site": "siet-ph1", "device": "CLOUD", "cmdId": str(uuid.uuid4()), "kind": kind,
            "issuedAt": stamp(now), "expiresAt": stamp(now + timedelta(seconds=60)),
            "issuedBy": "user:42", **body}


def dose(**lines):
    return command("DOSE", target={"zone": "Z1"},
                   lines=[{"line": k, "ml": v} for k, v in lines.items()], order=list(lines),
                   mixWaitS=0, maxDurationS=600, tolerancePct=5)


class Live:
    def __init__(self, broker, tmp_path):
        self.settings = dataclasses.replace(broker, client_dir=make_rig_pack(tmp_path),
                                            state_dir=tmp_path / "state")
        base = f"m/test{uuid.uuid4().hex[:8]}/c"  # a fresh rig topic space for each test
        self.rig = RigSettings(f"{base}/telemetry", f"{base}/relay-cmd", "RIGLINK", 90.0)
        self.received: list[dict] = []
        self.verdicts: list[tuple[str, str]] = []

    async def __aenter__(self):
        self.link, self.devices = build(self.settings, self.rig)
        self.boot_id = self.devices[0].envelope.boot_id
        self.fake = FakeRig(self.settings, self.rig, interval_s=1.0, time_scale=20.0)
        self.cloud = aiomqtt.Client(self.settings.mqtt_host, self.settings.mqtt_port,
                                    identifier=f"test-cloud-{uuid.uuid4().hex[:8]}")
        await self.cloud.__aenter__()
        await self.cloud.subscribe("farm/siet-ph1/#", qos=1)
        self.tasks = [asyncio.create_task(self._listen())]
        self.tasks += [asyncio.create_task(d.run(self.settings)) for d in self.devices]
        self.tasks += [asyncio.create_task(self.link.run(self.settings)),
                       asyncio.create_task(tick_forever(self.devices)),
                       asyncio.create_task(self.fake.run())]
        await self.wait_for(lambda m: m["type"] == "ZONE_TELEMETRY"
                            and m["quality"].get("ec") == "OK")
        return self

    async def __aexit__(self, *exc):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.cloud.__aexit__(None, None, None)

    async def _listen(self):
        async for message in self.cloud.messages:
            if message.retain:
                continue  # left by earlier runs
            payload = json.loads(message.payload)
            ours = payload.get("device") == "ZC1" and (
                payload.get("bootId") == self.boot_id or "bootId" not in payload)
            if ours:
                self.received.append(payload)
                self.verdicts.append(check(message.payload))

    async def send(self, topic, cmd):
        await self.cloud.publish(topic, json.dumps(cmd), qos=1)

    async def wait_for(self, done, timeout_s=20.0):
        async with asyncio.timeout(timeout_s):
            while not (found := [m for m in self.received if done(m)]):
                await asyncio.sleep(0.05)
        return found

    def states(self, cmd):
        return [m for m in self.received if m.get("cmdId") == cmd["cmdId"]]


def all_ok(live):
    assert live.verdicts and all(v == "OK" for v, _ in live.verdicts), live.verdicts


def test_readings_arrive_and_a_dose_runs_the_right_relay_for_the_right_mL(broker, run, tmp_path):
    async def scenario():
        async with Live(broker, tmp_path) as live:
            cmd = dose(A=120)  # 60 s at 2 mL/s: 3 real seconds for the fake rig
            await live.send(ZONE_CMD, cmd)
            await live.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"] and m.get("after"))
            return live, cmd

    live, cmd = run(scenario())
    reading = next(m for m in live.received if m["type"] == "ZONE_TELEMETRY")
    assert reading["quality"]["ec"] == "OK"  # never a reading of nothing before the rig's first
    assert (reading["zone"], reading["readings"]["ec"], reading["readings"]["ph"]) == (
        "Z1", 1.3, 6.1)
    [sent] = [c for c in live.fake.commands if c["cmd"] == "dose"]
    assert (sent["target"], sent["args"]["relay"], sent["args"]["seconds"]) == (
        "zone:1", "relay1", 60.0)
    final = live.states(cmd)[-1]
    assert final["state"] == "COMPLETED"
    assert [(x["line"], x["deliveredMl"], x["state"]) for x in final["lines"]] == [
        ("A", 120, "DONE")]
    all_ok(live)


def test_stop_mid_dose_switches_the_relay_off_and_reports_what_ran(broker, run, tmp_path):
    async def scenario():
        async with Live(broker, tmp_path) as live:
            cmd = dose(A=300)  # 150 s: 7.5 real seconds
            await live.send(ZONE_CMD, cmd)
            await live.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"]
                                and m.get("state") == "EXECUTING")
            await asyncio.sleep(1.5)
            stop = command("STOP", target={"scope": "ZONE", "zone": "Z1"})
            await live.send(STOP_TOPIC, stop)
            await live.wait_for(lambda m: m.get("cmdId") == cmd["cmdId"]
                                and m.get("state") == "STOPPED")
            return live, cmd

    live, cmd = run(scenario())
    final = live.states(cmd)[-1]
    assert final["reason"] == "STOP_REQUESTED"
    assert 0 < final["lines"][0]["deliveredMl"] < 300  # measured by the rig, not guessed
    off = [c for c in live.fake.commands if c["cmd"] == "switch"]
    assert off and off[0]["args"] == {"relays": {"relay1": False}}
    all_ok(live)


def test_rig_silence_shows_zc1_offline(broker, run, tmp_path):
    async def scenario():
        async with Live(broker, tmp_path) as live:
            live.fake.go_silent()  # its last will reaches the service at once
            await live.wait_for(lambda m: m["type"] == "DEVICE_STATUS" and m["online"] is False)
            return live

    live = run(scenario())
    will = next(m for m in live.received if m["type"] == "DEVICE_STATUS" and not m["online"])
    assert will == {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False}
    all_ok(live)
