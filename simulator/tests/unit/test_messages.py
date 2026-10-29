"""Envelope and DEVICE_STATUS, field for field against the contract (T4-2)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

from sim.messages import Envelope, device_status, offline_status, status_topic, utc_ts

T0 = datetime(2026, 11, 9, 4, 54, 0, 123456, tzinfo=UTC)
ENVELOPE_KEYS = {"v", "type", "msgId", "ts", "site", "device", "bootId", "seq"}  # contract 5
STATUS_KEYS = ENVELOPE_KEYS | {  # contract 6.4
    "online", "fw", "fwHash", "uptimeS", "rssiDbm", "clockSynced", "configVersion", "freeHeapKb",
}


def test_time_is_utc_with_milliseconds_and_z():
    assert utc_ts(T0) == "2026-11-09T04:54:00.123Z"
    ist = timezone(timedelta(hours=5, minutes=30))
    assert utc_ts(T0.astimezone(ist)) == "2026-11-09T04:54:00.123Z"  # people see IST, machines UTC


def test_envelope_has_every_contract_field_and_counts_seq():
    env = Envelope("siet-ph1", "ZC1", boot_id=412)
    first, second = env.next("DEVICE_STATUS", T0), env.next("ZONE_TELEMETRY", T0)

    assert set(first) == ENVELOPE_KEYS
    assert first | {"msgId": None} == {
        "v": 1, "type": "DEVICE_STATUS", "msgId": None, "ts": "2026-11-09T04:54:00.123Z",
        "site": "siet-ph1", "device": "ZC1", "bootId": 412, "seq": 1,
    }
    assert second["seq"] == 2  # seq counts every message of the boot, whatever its type
    for message in (first, second):
        assert uuid.UUID(message["msgId"]).version == 4
    assert first["msgId"] != second["msgId"]


def test_device_status_matches_contract_6_4():
    status = device_status(Envelope("siet-ph1", "ZC1", 1).next("DEVICE_STATUS", T0),
                           uptime_s=86400, gateway=False)
    assert set(status) == STATUS_KEYS
    assert status["online"] is True and status["clockSynced"] is True
    assert status["uptimeS"] == 86400
    assert status["rssiDbm"] is None and status["freeHeapKb"] is None  # no fake hardware values


def test_gateway_also_reports_its_buffer_and_cloud_link():
    status = device_status(Envelope("siet-ph1", "GW1", 1).next("DEVICE_STATUS", T0),
                           uptime_s=5, gateway=True)
    assert (status["bufferedMessages"], status["cloudLink"]) == (0, "UP")


def test_last_will_is_exactly_the_contract_payload():
    assert offline_status("ZC1") == {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1",
                                     "online": False}


def test_status_topic():
    assert status_topic("siet-ph1", "PM1") == "farm/siet-ph1/device/PM1/status"
