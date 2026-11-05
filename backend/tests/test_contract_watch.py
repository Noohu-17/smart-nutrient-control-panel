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


# A CMD_STATE as the simulator's zone controller sends it (contract 6.6, T4-8).
SIMULATOR_CMD_STATE = {
    "v": 1, "type": "CMD_STATE", "msgId": "0b7f2c1e-5b8e-4a59-9c4e-0c8d3a7f1a21",
    "ts": "2026-11-09T04:55:02.000Z", "site": "siet-ph1", "device": "ZC1", "bootId": 4, "seq": 9,
    "cmdId": "6f1c2a90-3d1e-4c7b-9f52-2b8f0e6a1c44", "state": "COMPLETED", "reason": None,
    "lines": [{"line": "A", "requestedMl": 120, "deliveredMl": 121, "state": "DONE"}],
    "before": {"ec": 1.51, "ph": 6.21}, "after": {"ec": 1.57, "ph": 6.2},
    "startedAt": "2026-11-09T04:55:00.000Z", "endedAt": "2026-11-09T04:56:00.000Z",
}


def test_cmd_state_has_no_model_yet_so_its_shape_is_checked():
    result, detail = check(payload(SIMULATOR_CMD_STATE))
    assert result == "OK"
    assert "shape" in detail


def test_a_rejected_cmd_state_with_its_reason_passes():
    rejected = SIMULATOR_CMD_STATE | {"state": "REJECTED", "reason": "ESTOP", "before": None,
                                      "after": None, "startedAt": None, "endedAt": None}
    assert check(payload(rejected))[0] == "OK"


def test_a_cmd_state_breaking_the_reason_rule_fails():
    assert check(payload(SIMULATOR_CMD_STATE | {"state": "REJECTED"}))[0] == "FAIL"
    assert check(payload(SIMULATOR_CMD_STATE | {"reason": "ESTOP"}))[0] == "FAIL"


def test_a_cmd_state_with_a_bad_field_fails_and_names_it():
    result, detail = check(payload(SIMULATOR_CMD_STATE | {"state": "DONE"}))
    assert result == "FAIL" and "state" in detail
    broken_line = [{"line": "A", "requestedMl": 120, "deliveredMl": "121", "state": "DONE"}]
    result, detail = check(payload(SIMULATOR_CMD_STATE | {"lines": broken_line}))
    assert result == "FAIL" and "deliveredMl" in detail
    assert check(payload(SIMULATOR_CMD_STATE | {"cmdId": "x"}))[0] == "FAIL"


def test_a_payload_that_is_not_a_json_object_fails():
    assert check(b"not json")[0] == "FAIL"
    assert check(b"[1, 2]")[0] == "FAIL"
