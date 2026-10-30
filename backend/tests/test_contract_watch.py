"""The broker watcher's verdict on one raw MQTT payload (app/contract/watch.py)."""

import json

from app.contract.watch import check

# A DEVICE_STATUS heartbeat as the simulator sends it (simulator/sim/messages.py, T4-2).
SIMULATOR_HEARTBEAT = {
    "v": 1, "type": "DEVICE_STATUS", "msgId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44",
    "ts": "2026-10-29T06:12:00.123Z", "site": "siet-ph1", "device": "GW1", "bootId": 4, "seq": 1,
    "simTs": "2026-10-29T06:12:00.123Z",
    "online": True, "fw": "sim-0.1.0", "fwHash": None, "uptimeS": 0, "rssiDbm": None,
    "clockSynced": True, "configVersion": None, "freeHeapKb": None,
    "bufferedMessages": 0, "cloudLink": "UP",
}


def payload(message) -> bytes:
    return json.dumps(message).encode()


def test_a_simulator_heartbeat_passes():
    assert check(payload(SIMULATOR_HEARTBEAT)) == ("OK", "DEVICE_STATUS from GW1")


def test_the_last_will_passes():
    last_will = {"v": 1, "type": "DEVICE_STATUS", "device": "ZC1", "online": False}
    assert check(payload(last_will)) == ("OK", "DEVICE_STATUS from ZC1")


def test_a_broken_message_fails_and_names_the_field():
    result, detail = check(payload(SIMULATOR_HEARTBEAT | {"uptimeS": "12"}))
    assert result == "FAIL"
    assert "uptimeS" in detail


def test_a_type_without_a_model_is_skipped():
    assert check(payload({"v": 1, "type": "EVENT"}))[0] == "SKIP"


def test_a_payload_that_is_not_a_json_object_fails():
    assert check(b"not json")[0] == "FAIL"
    assert check(b"[1, 2]")[0] == "FAIL"
