"""The service's link to the rig and its start (T1-32 review): a publish that fails ends the
session so it reconnects, a full outbox says what it drops, and a pack with no zone on the rig
stops the service cleanly."""

from __future__ import annotations

import asyncio

import aiomqtt
import pytest

from sim.rig import service
from sim.rig.config import RigSettings
from sim.rig.link import COMMANDS_HELD, RigLink
from sim.settings import load_settings

SETTINGS = RigSettings("m/dev/c/telemetry", "m/dev/c/relay-cmd", "RIGLINK", 90.0)


def command(n: int) -> dict:
    return {"schema": "sf/cmd/1", "id": f"c{n}", "cmd": "switch", "target": "zone:1", "args": {}}


class TimingOutClient:
    """A link that stays up, delivers nothing, and times out on every publish."""

    async def subscribe(self, topic, qos=0):
        return None

    async def publish(self, topic, payload, qos=0, timeout=None):
        raise aiomqtt.MqttError("Operation timed out")

    @property
    def messages(self):
        return self._nothing()

    async def _nothing(self):
        await asyncio.Event().wait()
        yield  # never reached: an async generator that waits forever


def test_a_publish_that_fails_ends_the_session_with_the_command_still_first():
    link = RigLink(SETTINGS)
    link.send(command(1))

    async def session():
        await asyncio.wait_for(link._session(TimingOutClient()), timeout=5)

    with pytest.raises(aiomqtt.MqttError):  # connect_forever then connects again
        asyncio.run(session())
    assert [c["id"] for c in link._outbox] == ["c1"]  # sent first after the reconnect


def test_a_full_outbox_drops_the_oldest_command_and_says_so(caplog):
    link = RigLink(SETTINGS)
    for n in range(COMMANDS_HELD + 1):
        link.send(command(n))
    assert len(link._outbox) == COMMANDS_HELD and link._outbox[0]["id"] == "c1"
    assert "dropping the oldest: switch zone:1 c0" in caplog.text


def test_with_no_zone_on_the_rig_the_service_says_so_and_stops_cleanly(monkeypatch, caplog):
    """client_A has no rig zone: main() returns (exit 0), so Compose's restart: on-failure
    leaves it stopped instead of restarting it in a loop."""
    monkeypatch.setattr(service, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("SIM_CLIENT_DIR", str(load_settings({}).client_dir))
    service.main()
    assert "no zone runs on the rig" in caplog.text and "has nothing to run" in caplog.text
