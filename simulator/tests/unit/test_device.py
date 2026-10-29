"""A device's heartbeat, last will and clean stop, with a fake MQTT client (T4-2)."""

from __future__ import annotations

import asyncio
import json

import aiomqtt
import pytest

from sim.devices.base import SimDevice


class FakeClient:
    """Records publishes; its message stream ends with a disconnect when told to."""

    def __init__(self):
        self.published = []
        self.disconnect = asyncio.Event()

    async def publish(self, topic, payload, qos, retain, timeout=None):
        self.published.append({"topic": topic, "qos": qos, "retain": retain,
                               "message": json.loads(payload)})

    @property
    def messages(self):
        return self._messages()

    async def _messages(self):
        await self.disconnect.wait()
        raise aiomqtt.MqttError("Disconnected during message iteration")
        yield  # makes this an async generator, like aiomqtt's


def make_device(device_id="ZC1", kind="ZONE_CONTROLLER", heartbeat_s=0.01):
    clock = iter(range(1000))
    return SimDevice(device_id, kind, "siet-ph1", boot_id=3, heartbeat_s=heartbeat_s,
                     monotonic=lambda: float(next(clock)))


def test_heartbeats_are_retained_qos1_on_the_status_topic():
    async def scenario():
        client, device = FakeClient(), make_device()
        task = asyncio.create_task(device.session(client))
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return client.published

    published = asyncio.run(scenario())
    beats = [p for p in published if p["message"]["online"]]
    assert len(beats) >= 3
    assert all(p["topic"] == "farm/siet-ph1/device/ZC1/status" for p in published)
    assert all(p["qos"] == 1 and p["retain"] is True for p in published)
    assert [b["message"]["seq"] for b in beats] == list(range(1, len(beats) + 1))
    assert {b["message"]["bootId"] for b in beats} == {3}


def test_a_clean_stop_publishes_offline_because_the_will_is_discarded():
    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(make_device(heartbeat_s=60).session(client))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return client.published

    published = asyncio.run(scenario())
    assert published[-1]["message"] == {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1",
                                        "online": False}


def test_a_dropped_link_ends_the_session_so_connect_forever_reconnects():
    async def scenario():
        client = FakeClient()
        task = asyncio.create_task(make_device(heartbeat_s=60).session(client))
        await asyncio.sleep(0.05)
        client.disconnect.set()
        await task

    with pytest.raises(aiomqtt.MqttError):
        asyncio.run(scenario())


def test_the_last_will_is_the_contract_offline_payload_retained():
    will = make_device("PM1", "POWER_MONITOR").will()
    assert will.topic == "farm/siet-ph1/device/PM1/status"
    assert (will.qos, will.retain) == (1, True)
    assert json.loads(will.payload) == {"v": 1, "type": "DEVICE_STATUS", "device": "PM1",
                                        "online": False}


def test_only_the_gateway_reports_buffer_and_cloud_link():
    assert "cloudLink" in make_device("GW1", "GATEWAY").status()
    assert "cloudLink" not in make_device("ZC1").status()


def test_uptime_counts_real_seconds_since_boot():
    device = make_device()  # fake monotonic: boot at 0, then 1, 2, ...
    assert device.status()["uptimeS"] == 1
    assert device.status()["uptimeS"] == 2
