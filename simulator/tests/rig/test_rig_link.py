"""The service's link to the rig and its start (T1-32 review): a publish that fails ends the
session so it reconnects, a full outbox says what it drops, and a pack with no zone on the rig
stops the service cleanly."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiomqtt
import pytest
from rig_support import T0, zone_doc

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


def test_client_b_runs_both_its_zones_through_one_gateway(tmp_path):
    """T1-31: ZC1 on the rig's Zone 1 and ZC2 on Zone 2, both through gateway-01. Each sf/zone
    document goes to the controller of the zone it names, as each dose report does."""
    client_b = Path(__file__).parents[3] / "config" / "clients" / "client_B"
    settings = load_settings({"SIM_CLIENT_DIR": str(client_b), "SIM_STATE_DIR": str(tmp_path)})
    link, devices = service.build(settings, SETTINGS)
    assert [(d.zone.device_id, d.zone.zone_id, d.zone.rig_zone) for d in devices] == [
        ("ZC1", "Z1", 1), ("ZC2", "Z2", 2)]
    for n in (2, 1):
        document = zone_doc(ts=T0, seq=50 + n) | {"zone": n, "name": f"Zone {n}"}
        link.on_message(f"{SETTINGS.telemetry_topic}/sf/zone/{n}", json.dumps(document),
                        retained=True)
    assert [d.hal.latest["zone"] for d in devices] == [1, 2]


def test_each_zones_updates_count_their_own_seq(tmp_path):
    """Zone 1's update 60 and zone 2's update 60 are two updates, not a QoS 1 repeat (the gateway
    counts each zone's seq on its own); the same zone's 60 again is a repeat and is dropped."""
    client_b = Path(__file__).parents[3] / "config" / "clients" / "client_B"
    settings = load_settings({"SIM_CLIENT_DIR": str(client_b), "SIM_STATE_DIR": str(tmp_path)})
    link, devices = service.build(settings, SETTINGS)
    for n, ph in ((1, 6.1), (2, 6.3), (2, 5.0)):  # the last is zone 2's 60 again, changed
        document = zone_doc(ts=T0, seq=60, water_quality={"ph": ph}) | {"zone": n}
        link.on_message(f"{SETTINGS.telemetry_topic}/sf/zone/{n}", json.dumps(document))
    assert [d.hal.latest["water_quality"]["ph"] for d in devices] == [6.1, 6.3]
