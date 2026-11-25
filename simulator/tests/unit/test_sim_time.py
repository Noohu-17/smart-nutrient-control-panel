"""ts stays real time, simTs carries simulated time on playground sites (T4-3)."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime

import pytest

from sim.clock import SimClock
from sim.config import load_speed_options, playground_enabled
from sim.devices.base import SimDevice
from sim.main import starting_speed
from sim.messages import Envelope
from sim.settings import load_settings

REAL = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self):
        self.statuses = []

    async def publish(self, topic, payload, qos, retain, timeout=None):
        self.statuses.append(json.loads(payload))

    @property
    def messages(self):
        return self._forever()

    async def _forever(self):
        await asyncio.Event().wait()
        yield


def test_envelope_adds_simTs_only_when_given():
    env = Envelope("siet-ph1", "ZC1", 1)
    assert "simTs" not in env.next("DEVICE_STATUS", REAL)
    message = env.next("DEVICE_STATUS", REAL, SIM_START)
    assert (message["ts"], message["simTs"]) == ("2026-10-29T09:00:00.000Z",
                                                 "2026-07-01T06:00:00.000Z")


def test_playground_device_status_carries_simTs_from_the_clock():
    real_s = [0.0]
    clock = SimClock(SIM_START, 288, (0, 1, 60, 288), monotonic=lambda: real_s[0])
    device = SimDevice("ZC1", "ZONE_CONTROLLER", "siet-ph1", 1, clock=clock, now=lambda: REAL)
    real_s[0] = 300.0  # five real minutes at x288
    status = device.status()
    assert status["ts"] == "2026-10-29T09:00:00.000Z"  # real time, untouched
    assert status["simTs"] == "2026-07-02T06:00:00.000Z"  # one simulated day later


def test_non_playground_device_has_no_simTs():
    assert "simTs" not in SimDevice("ZC1", "ZONE_CONTROLLER", "siet-ph1", 1).status()


def test_heartbeats_keep_real_time_while_the_clock_is_paused():
    clock = SimClock(SIM_START, 0, (0, 1, 60, 288))

    async def scenario():
        client = FakeClient()
        device = SimDevice("ZC1", "ZONE_CONTROLLER", "siet-ph1", 1, clock=clock,
                           heartbeat_s=0.01)
        task = asyncio.create_task(device.session(client))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return [s for s in client.statuses if s.get("online")]

    beats = asyncio.run(scenario())
    assert len(beats) >= 3  # paused clock, heartbeats still flowing
    assert {b["simTs"] for b in beats} == {"2026-07-01T06:00:00.000Z"}


def test_client_a_is_a_playground_site():
    assert playground_enabled(load_settings({}).client_dir) is True


def test_feature_switch_and_speed_options_are_checked(tmp_path):
    (tmp_path / "features.json").write_text('{"playground": "yes"}')
    with pytest.raises(ValueError, match="playground"):
        playground_enabled(tmp_path)
    (tmp_path / "features.json").write_text("{}")
    assert playground_enabled(tmp_path) is False
    (tmp_path / "simulator.json").write_text('{"speedOptions": [1, -60]}')
    with pytest.raises(ValueError, match="speedOptions"):
        load_speed_options(tmp_path)


@pytest.mark.parametrize(("text", "speed"), [("60", 60), ("288", 288), ("0", 0)])
def test_sim_speed_setting(text, speed):
    assert load_settings({"SIM_SPEED": text}).sim_speed == speed


def test_sim_speed_must_be_a_number():
    with pytest.raises(ValueError, match="SIM_SPEED"):
        load_settings({"SIM_SPEED": "fast"})


# --- the demo's speed (C-3) ------------------------------------------------------------


@pytest.mark.parametrize("env", [{}, {"SIM_SPEED": ""}, {"SIM_SPEED": "  "}])
def test_sim_speed_left_out_or_empty_is_not_set(env):
    assert load_settings(env).sim_speed is None


@pytest.mark.parametrize(
    ("env", "playground", "speed"),
    [
        ({}, True, 60),  # simulator.json demoStart.speed
        ({"SIM_SPEED": "288"}, True, 288),  # set: it wins
        ({"SIM_SPEED": ""}, True, 60),  # what the Compose files pass when it isn't set
        ({}, False, 1),  # not a playground: real time
    ],
)
def test_a_playground_starts_at_the_demo_speed_unless_sim_speed_is_set(env, playground, speed):
    assert starting_speed(load_settings(env), playground) == speed


def test_without_a_demo_speed_the_clock_runs_in_real_time(tmp_path):
    client_dir = shutil.copytree(load_settings({}).client_dir, tmp_path / "client")
    path = client_dir / "simulator.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["demoStart"]["speed"]
    path.write_text(json.dumps(data), encoding="utf-8")
    settings = load_settings({"SIM_CLIENT_DIR": str(client_dir)})
    assert starting_speed(settings, True) == 1
