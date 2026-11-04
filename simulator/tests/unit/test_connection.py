"""Device connections and reconnect, with a fake MQTT client (T4-1)."""

from __future__ import annotations

import asyncio
import logging
import random

import aiomqtt
import pytest

from sim.devices.base import (
    RECONNECT_JITTER,
    RECONNECT_MAX_S,
    connect_forever,
    jittered,
    reconnect_delay,
)
from sim.settings import load_settings


class FakeConnection:
    """Async context manager standing in for aiomqtt.Client."""

    def __init__(self, refuse: bool):
        self.refuse = refuse

    async def __aenter__(self):
        if self.refuse:
            raise aiomqtt.MqttError("connection refused")
        return self

    async def __aexit__(self, *exc):
        return False


class NoJitter:
    """Takes nothing off the wait, so a test can check the delays themselves."""

    def random(self):
        return 0.0


def run_connection(plan, sessions, env=None, device="ZC1", rng=None):
    """Run connect_forever with scripted connects; return (client kwargs, sleeps)."""
    calls, sleeps = [], []
    outcomes, session_results = iter(plan), iter(sessions)

    def factory(**kwargs):
        calls.append(kwargs)
        return FakeConnection(refuse=next(outcomes) == "refuse")

    async def session(client):
        result = next(session_results)
        if result == "drop":
            raise aiomqtt.MqttError("connection lost")

    async def sleep(seconds):
        sleeps.append(seconds)

    asyncio.run(connect_forever(load_settings(env or {}), device, session,
                                client_factory=factory, sleep=sleep, rng=rng))
    return calls, sleeps


def test_reconnect_delay_doubles_up_to_a_cap():
    delays = [reconnect_delay(n) for n in range(7)]
    assert delays == [1, 2, 4, 8, 16, RECONNECT_MAX_S, RECONNECT_MAX_S]


def test_logs_connected_and_reconnects_after_refusal_and_drop(caplog):
    caplog.set_level(logging.INFO, logger="sim.devices")
    calls, sleeps = run_connection(["refuse", "refuse", "ok", "ok"], ["drop", "done"],
                                   rng=NoJitter())

    assert len(calls) == 4
    assert sleeps == [1, 2, 1]  # the delay starts again after a successful connect
    assert sum("ZC1 connected to localhost:1883" in m for m in caplog.messages) == 2


REFUSALS = ["refuse"] * 7 + ["ok"]


def test_jitter_only_shortens_the_wait_so_the_cap_still_holds():
    rng = random.Random(1)
    for delay in (1.0, 2.0, 16.0, RECONNECT_MAX_S):
        for _ in range(1000):
            assert delay * (1 - RECONNECT_JITTER) <= jittered(delay, rng) <= delay


def test_jitter_really_varies_the_wait():
    rng = random.Random(1)
    assert len({jittered(30.0, rng) for _ in range(20)}) == 20


@pytest.mark.parametrize("device", ["ZC1", "ZC2", "CC1", "PM1", "GW1"])
def test_every_wait_stays_within_the_growing_delay(device):
    _, sleeps = run_connection(REFUSALS, ["done"], device=device)
    for attempt, wait in enumerate(sleeps):
        delay = reconnect_delay(attempt)
        assert delay * (1 - RECONNECT_JITTER) <= wait <= delay


def test_devices_do_not_retry_in_step_after_a_broker_restart():
    waits = {device: run_connection(REFUSALS, ["done"], device=device)[1]
             for device in ("GW1", "ZC1", "ZC2", "CC1", "PM1")}
    assert len({tuple(w) for w in waits.values()}) == len(waits)
    assert len({w[-1] for w in waits.values()}) == len(waits)


def test_a_devices_waits_repeat_exactly_from_run_to_run():
    # Track 4 guide section 12: all randomness comes from a seed; here, the device ID.
    assert (run_connection(REFUSALS, ["done"], device="ZC1")[1]
            == run_connection(REFUSALS, ["done"], device="ZC1")[1])


def test_connects_as_the_device_with_a_persistent_session():
    calls, _ = run_connection(["ok"], ["done"], env={"MQTT_PASSWORD_ZC1": "pw"})
    kwargs = calls[0]
    assert kwargs["identifier"] == "ZC1"
    assert (kwargs["username"], kwargs["password"]) == ("ZC1", "pw")
    assert kwargs["clean_session"] is False
    assert kwargs["protocol"] == aiomqtt.ProtocolVersion.V311
