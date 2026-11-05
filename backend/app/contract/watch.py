"""Watch the MQTT broker and check every farm message against the contract models.

Run from backend/ while the simulator is running:
    python -m app.contract.watch                # until Ctrl+C
    python -m app.contract.watch --seconds 60   # stop after 60 s

Prints one line per message: OK, FAIL with the fields that break the contract, or
SKIP for a message type that has no model yet. Exits 1 if any message failed. The
broker address comes from MQTT_HOST and MQTT_PORT, as for the API.

CMD_STATE has no model yet, so its shape is checked instead (CmdStateShape): its envelope,
fields, enums and the reason rule of contract 6.6. Delete the stand-in when the model exists.
"""

import argparse
import json
import time
import uuid
from typing import Any, Literal

import paho.mqtt.client as mqtt
from pydantic import UUID4, ValidationError, model_validator

from ..config import load_settings
from .envelope import ContractModel, Count, Envelope, Number, UtcTime
from .farm_messages import CmdState, parse_farm_message

TOPIC = "farm/#"
STATES_WITH_A_REASON = {"PARTIAL", "FAILED", "STOPPED", "REJECTED"}  # contract 6.6


class _LineShape(ContractModel):
    line: str
    requested_ml: Count
    delivered_ml: Count
    state: Literal["PENDING", "RUNNING", "DONE", "SHORT", "SKIPPED"]


class _EcPhShape(ContractModel):
    ec: Number | None
    ph: Number | None


class CmdStateShape(Envelope):
    """CMD_STATE (contract 6.6), checked for shape until Track 1 writes its model."""

    type: Literal["CMD_STATE"]
    cmd_id: UUID4
    state: CmdState
    reason: str | None
    lines: list[_LineShape] | None = None  # DOSE only
    before: _EcPhShape | None
    after: _EcPhShape | None
    started_at: UtcTime | None
    ended_at: UtcTime | None
    detail: dict[str, Any] | None = None  # refill and climate commands

    @model_validator(mode="after")
    def _reason_only_where_6_6_gives_one(self) -> "CmdStateShape":
        if (self.reason is not None) != (self.state in STATES_WITH_A_REASON):
            raise ValueError(f"a {self.state} CMD_STATE {'needs' if self.reason is None else 'has no'} reason")
        return self


SHAPE_CHECKS = {"CMD_STATE": CmdStateShape}


def check(payload: bytes) -> tuple[str, str]:
    """("OK" | "FAIL" | "SKIP", detail) for one raw MQTT payload."""
    try:
        message = json.loads(payload)
    except ValueError as exc:
        return "FAIL", f"not JSON: {exc}"
    if not isinstance(message, dict):
        return "FAIL", "not a JSON object"
    kind = message.get("type")
    try:
        parse_farm_message(message)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return "FAIL", f"{kind} from {message.get('device')}: {problems}"
    except ValueError:
        if kind not in SHAPE_CHECKS:
            return "SKIP", f"{kind}: no contract model yet"
        try:
            SHAPE_CHECKS[kind].model_validate(message)
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            return "FAIL", f"{kind} from {message.get('device')} (shape): {problems}"
        return "OK", f"{kind} from {message.get('device')} (shape; no contract model yet)"
    return "OK", f"{kind} from {message.get('device')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, help="stop after this many seconds (default: until Ctrl+C)")
    args = parser.parse_args(argv)
    settings = load_settings()
    counts = {"OK": 0, "FAIL": 0, "SKIP": 0}

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe(TOPIC, qos=1)  # again after every reconnect

    def on_message(client, userdata, msg):
        result, detail = check(msg.payload)
        counts[result] += 1
        print(f"{result:4}  {msg.topic}  {detail}", flush=True)

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"CLOUD-contract-watch-{uuid.uuid4().hex[:8]}",  # never takes over CLOUD-api's session
        protocol=mqtt.MQTTv311,
        clean_session=True,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    try:
        client.connect(settings.mqtt_host, settings.mqtt_port, keepalive=30)
    except OSError as exc:
        print(f"cannot reach the broker at {settings.mqtt_host}:{settings.mqtt_port}: {exc}")
        return 2
    print(f"watching {TOPIC} on {settings.mqtt_host}:{settings.mqtt_port}", flush=True)
    client.loop_start()
    deadline = None if args.seconds is None else time.monotonic() + args.seconds
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        client.loop_stop()
    print(f"{counts['OK']} OK, {counts['FAIL']} failed, {counts['SKIP']} skipped (no model yet)")
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
