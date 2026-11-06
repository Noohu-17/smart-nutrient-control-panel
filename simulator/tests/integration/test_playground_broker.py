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


def dose(**lines):
    now = datetime.now(UTC)
    return {
        "v": 1, "type": "CMD", "msgId": str(uuid.uuid4()), "ts": stamp(now), "site": "siet-ph1",
        "device": "CLOUD", "cmdId": str(uuid.uuid4()), "kind": "DOSE", "issuedAt": stamp(now),
        "expiresAt": stamp(now + timedelta(seconds=60)), "issuedBy": "user:42",
        "target": {"zone": "Z1"}, "lines": [{"line": k, "ml": v} for k, v in lines.items()],
        "order": list(lines), "mixWaitS": 60, "maxDurationS": 600, "tolerancePct": 5,
    }


class Sim:
    """The simulator in-process with its control API, and a cloud client that records."""

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
        self.clock = SimClock(datetime.now(UTC), SPEED, load_speed_options(client_dir))
        farm = load_farm(client_dir)
        farm.now = self.clock.last_tick
        alerts = read_pack_file(client_dir, "alerts.json")
        devices = [make_device(d, site, BOOT_ID, farm, alerts, self.clock, client_dir=client_dir,
                               state_dir=self.state_dir) for d in load_devices(client_dir)]
        min_real_s = load_min_real_reading_interval_s(client_dir)
        self.playground = Playground(farm, self.clock, client_dir,
                                     controllers=controllers_of(devices),
                                     device_ids=[d.id for d in devices],
                                     min_real_reading_s=min_real_s)
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
            if payload.get("bootId") == BOOT_ID:
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
    assert (after["readings"]["ec"], after["readings"]["reservoirPct"]) == (1.10, 78)
    all_ok(sim)
