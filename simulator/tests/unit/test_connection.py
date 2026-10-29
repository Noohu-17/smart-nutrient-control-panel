"""Device connections and reconnect, with a fake MQTT client (T4-1)."""

from __future__ import annotations

import asyncio
import logging

import aiomqtt

from sim.devices.base import RECONNECT_MAX_S, connect_forever, reconnect_delay
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


def run_connection(plan, sessions, env=None):
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

    asyncio.run(connect_forever(load_settings(env or {}), "ZC1", session,
                                client_factory=factory, sleep=sleep))
    return calls, sleeps


def test_reconnect_delay_doubles_up_to_a_cap():
    delays = [reconnect_delay(n) for n in range(7)]
    assert delays == [1, 2, 4, 8, 16, RECONNECT_MAX_S, RECONNECT_MAX_S]


def test_logs_connected_and_reconnects_after_refusal_and_drop(caplog):
    caplog.set_level(logging.INFO, logger="sim.devices")
    calls, sleeps = run_connection(["refuse", "refuse", "ok", "ok"], ["drop", "done"])

    assert len(calls) == 4
    assert sleeps == [1, 2, 1]  # the delay starts again after a successful connect
    assert sum("ZC1 connected to localhost:1883" in m for m in caplog.messages) == 2


def test_connects_as_the_device_with_a_persistent_session():
    calls, _ = run_connection(["ok"], ["done"], env={"MQTT_PASSWORD_ZC1": "pw"})
    kwargs = calls[0]
    assert kwargs["identifier"] == "ZC1"
    assert (kwargs["username"], kwargs["password"]) == ("ZC1", "pw")
    assert kwargs["clean_session"] is False
    assert kwargs["protocol"] == aiomqtt.ProtocolVersion.V311
