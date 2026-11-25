"""A device connects to a real broker and logs "connected" (T4-1)."""

from __future__ import annotations

import logging

import pytest

from sim.devices.base import connect_forever

pytestmark = pytest.mark.broker


def test_device_connects_and_logs_connected(broker, run, caplog):
    caplog.set_level(logging.INFO, logger="sim.devices")

    async def leave_at_once(client):
        return None

    run(connect_forever(broker, "ZC1", leave_at_once))
    assert f"ZC1 connected to {broker.mqtt_host}:{broker.mqtt_port}" in caplog.messages
