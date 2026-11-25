"""A stand-in for the prototype rig's gateway, for development and tests (T1-32).

It speaks sf schema 1 as gateway 1.0.0 does (snm-prototype-extension-v1, gateway docs/CLOUD.md and
docs/schemas): its status online, retained, with offline as its last will; a heartbeat and a zone
update per zone every interval, and a zone update at once after a change. It obeys `dose`
(running, then done once its seconds have passed), `switch` OFF (a running dose ends "stopped",
reason "command") and `estop`, and answers every command with sf/cmd-result/1, refusing a
repeated id, a stale command and a busy relay as the rig does. It is not the rig: nothing is
pumped, and readings stay where they are set.

    python -m sim.rig.fake [--zones 1 2] [--interval 30] [--time-scale 1]

Same settings as the service (MQTT_HOST, RIG_TELEMETRY_TOPIC, RIG_COMMAND_TOPIC). --time-scale 10
runs a 60 s dose in 6 real seconds; it still reports 60 s, as the rig would.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import aiomqtt
from dotenv import load_dotenv

from ..devices.base import drop_link
from ..settings import SIM_ROOT, Settings, load_settings
from . import sf
from .config import RigSettings, load_rig_settings

log = logging.getLogger("sim.rig.fake")

FW = {"version": "1.0.0", "rev": "fake0000", "state": "confirmed", "proto": 1}
RELAYS = ("relay1", "relay2", "relay3", "relay4")


@dataclass
class FakeZone:
    ph: float | None = 6.1
    tds_ppm: float | None = 650.0
    temp_c: float | None = 24.5
    level_pct: float | None = 62.5
    liters: float | None = 56.0  # 62.5 % of client_B's 90 L main tank
    estop: bool = False
    relays: dict[str, bool] = field(default_factory=lambda: dict.fromkeys(RELAYS, False))


@dataclass
class FakeDose:
    dose_id: str
    relay: str
    seconds: float
    started: float
    task: asyncio.Task[None] | None = None


class FakeRig:
    def __init__(
        self,
        settings: Settings,
        rig: RigSettings,
        *,
        gateway: str = "gateway-01",
        zones: Iterable[int] = (1,),
        boot: int = 1,
        interval_s: float = 30.0,
        time_scale: float = 1.0,
        client_id: str = "FAKEGATEWAY",
    ):
        self.settings, self.rig, self.gateway = settings, rig, gateway
        self.zones = {zone: FakeZone() for zone in zones}
        self.boot, self.interval_s, self.time_scale = boot, interval_s, time_scale
        self.client_id = client_id
        self.commands: list[dict[str, Any]] = []  # every command received, for tests
        self.doses: dict[tuple[int, str], FakeDose] = {}
        self._ids: set[str] = set()
        self._seq: Counter[str] = Counter()
        self._started = time.monotonic()
        self._client: aiomqtt.Client | None = None
        self._awake = asyncio.Event()
        self._awake.set()

    # --- what a test or a person changes -----------------------------------------------------

    def set_readings(self, zone: int, **values: Any) -> None:
        for key, value in values.items():
            setattr(self.zones[zone], key, value)
        self._zone_now(zone)

    def go_silent(self) -> None:
        """Power off: the link drops without a goodbye, so the broker publishes the last will."""
        self._awake.clear()
        for dose in list(self.doses.values()):
            if dose.task:
                dose.task.cancel()
        self.doses.clear()
        if self._client is not None:
            drop_link(self._client)

    def come_back(self, *, restarted: bool = False) -> None:
        """Power on again; a restart is a new boot with every relay off."""
        if restarted:
            self.boot += 1
            self._seq.clear()
            self._started = time.monotonic()
            for zone in self.zones.values():
                zone.relays = dict.fromkeys(RELAYS, False)
        self._awake.set()

    # --- the MQTT side ---------------------------------------------------------------------

    async def run(self) -> None:
        will = aiomqtt.Will(self._topic(sf.STATUS_SUBTOPIC), _status("offline", self.gateway),
                            qos=1, retain=True)
        while True:
            await self._awake.wait()
            try:
                async with aiomqtt.Client(self.settings.mqtt_host, self.settings.mqtt_port,
                                          identifier=self.client_id, will=will) as client:
                    self._client = client
                    await self._session(client)
            except aiomqtt.MqttError as err:
                log.info("fake rig link down (%s)", err)
                await asyncio.sleep(0.5)
            finally:
                self._client = None

    async def _session(self, client: aiomqtt.Client) -> None:
        await client.subscribe(f"{self.rig.command_topic}/{sf.COMMAND_SUBTOPIC}", qos=1)
        await client.publish(self._topic(sf.STATUS_SUBTOPIC), _status("online", self.gateway),
                             qos=1, retain=True)
        beat = asyncio.create_task(self._every_interval())
        try:
            async for message in client.messages:
                command = sf.parse(message.payload)
                if isinstance(command, dict):
                    self._command(command)
        finally:
            beat.cancel()

    async def _every_interval(self) -> None:
        while True:
            self._publish(self._topic("sf/gateway/heartbeat"), self._heartbeat(), retain=True)
            for zone in self.zones:
                self._zone_now(zone)
            await asyncio.sleep(self.interval_s)

    def _publish(self, topic: str, document: dict[str, Any], *, retain: bool = False) -> None:
        if self._client is not None and self._awake.is_set():
            asyncio.ensure_future(self._client.publish(topic, json.dumps(document), qos=1,
                                                       retain=retain))

    def _topic(self, subtopic: str) -> str:
        return f"{self.rig.telemetry_topic}/{subtopic}"

    # --- commands, as the gateway handles them -----------------------------------------------

    def _command(self, command: dict[str, Any]) -> None:
        self.commands.append(command)
        command_id, cmd = command.get("id"), command.get("cmd")
        if command_id in self._ids:
            self._result(command, "duplicate", "duplicate")
            return
        self._ids.add(command_id)
        zone = _zone_of(command.get("target"))
        if zone not in self.zones:
            self._result(command, "invalid", "bad-request")
        elif cmd == "dose":
            self._dose(zone, command)
        elif cmd == "switch":
            for relay, on in (command.get("args") or {}).get("relays", {}).items():
                if not on:
                    self._end(zone, relay, "stopped", "command")
                    self.zones[zone].relays[relay] = False
            self._result(command, "ok", "ok")
            self._zone_now(zone)
        elif cmd == "estop":
            engage = bool((command.get("args") or {}).get("engage"))
            self.zones[zone].estop = engage
            if engage:
                for relay in RELAYS[:3]:
                    self._end(zone, relay, "stopped", "estop")
            self._result(command, "ok", "ok")
            self._zone_now(zone)
        else:
            self._result(command, "unsupported", "unsupported")

    def _dose(self, zone: int, command: dict[str, Any]) -> None:
        args = command.get("args") or {}
        relay, seconds, dose_id = args.get("relay"), args.get("seconds"), args.get("dose_id")
        reason = None
        if time.time() - float(command.get("issued_at") or 0) > 60:
            reason = "stale"
        elif self.zones[zone].estop:
            reason = "estop"
        elif (zone, relay) in self.doses:
            reason = "busy"
        elif not isinstance(seconds, (int, float)) or not 0.1 <= seconds <= 600:
            reason = "duration"
        if reason:
            self._dose_report(zone, dose_id, relay, "rejected", reason, seconds or 0, 0)
            self._result(command, "refused", reason)
            return
        dose = FakeDose(dose_id, relay, float(seconds), time.monotonic())
        self.doses[(zone, relay)] = dose
        self.zones[zone].relays[relay] = True
        self._result(command, "ok", "ok")
        self._dose_report(zone, dose_id, relay, "running", None, seconds, 0)
        self._zone_now(zone)
        dose.task = asyncio.ensure_future(self._finish_later(zone, dose))

    async def _finish_later(self, zone: int, dose: FakeDose) -> None:
        await asyncio.sleep(dose.seconds / self.time_scale)
        self._end(zone, dose.relay, "done", None)
        self._zone_now(zone)

    def _end(self, zone: int, relay: str, state: str, reason: str | None) -> None:
        dose = self.doses.pop((zone, relay), None)
        if dose is None:
            return
        if dose.task and state != "done":
            dose.task.cancel()
        self.zones[zone].relays[relay] = False
        ran = dose.seconds if state == "done" else min(
            dose.seconds, (time.monotonic() - dose.started) * self.time_scale)
        self._dose_report(zone, dose.dose_id, relay, state, reason, dose.seconds, round(ran, 3))

    # --- sf documents ----------------------------------------------------------------------

    def _doc(self, schema: str, key: str | None = None, **fields: Any) -> dict[str, Any]:
        self._seq[key or schema] += 1
        return {"schema": schema, "gateway": self.gateway, "boot": self.boot,
                "seq": self._seq[key or schema], **fields, "ts": int(time.time())}

    def _result(self, command: dict[str, Any], status: str, result: str) -> None:
        self._publish(self._topic("sf/cmd/result"), self._doc(
            sf.CMD_RESULT, id=str(command.get("id")), target=str(command.get("target")),
            cmd=str(command.get("cmd")), status=status, result=result, value=None, detail=None))

    def _dose_report(self, zone: int, dose_id: Any, relay: Any, state: str, reason: str | None,
                     duration: float, elapsed: float) -> None:
        self._publish(self._topic("sf/dose"), self._doc(
            sf.DOSE, zone=zone, device="actuator-node-01" if zone == 1 else self.gateway,
            dose_id=str(dose_id), relay=relay, state=state, reason=reason,
            duration_s=duration, elapsed_s=elapsed))

    def _zone_now(self, zone: int) -> None:
        self._publish(self._topic(f"sf/zone/{zone}"), self._zone(zone), retain=True)

    def _zone(self, number: int) -> dict[str, Any]:
        zone, now = self.zones[number], int(time.time())
        source = "sensor-node-01" if number == 1 else self.gateway
        actuator = "actuator-node-01" if number == 1 else self.gateway
        return self._doc(
            sf.ZONE, f"zone{number}", zone=number, name=f"Zone {number}", uptime_s=self._uptime(),
            health="ok",
            water_quality={"source": source, "seq": self._seq[f"zone{number}"], "ts": now,
                           "age_s": 1, "ph": zone.ph, "ph_volt": 1.58, "tds_ppm": zone.tds_ppm,
                           "tds_volt": 1.123, "temp_c": zone.temp_c, "samples": 30},
            tank={"source": actuator, "age_s": 1, "sensor_ok": zone.level_pct is not None,
                  "level_pct": zone.level_pct, "distance_cm": 33.1, "liters": zone.liters,
                  "inlet_on": zone.relays["relay4"], "auto_fill": True, "fill_fault": None,
                  "on_pct": 50, "off_pct": 75},
            actuators={"source": actuator, "age_s": 1, "relays": dict(zone.relays),
                       "flow_pct": dict.fromkeys(RELAYS, 0), "flow_period_s": 30,
                       "dosing": {r: (number, r) in self.doses for r in RELAYS[:3]},
                       "estop": zone.estop, "failsafe": False, "one_pass": False},
            devices=[], alarms=[])

    def _heartbeat(self) -> dict[str, Any]:
        return self._doc(
            sf.HEARTBEAT, uptime_s=self._uptime(), interval_s=round(self.interval_s), health="ok",
            fw=FW, boot_info={"count": self.boot, "reset": "power-on"},
            link={"type": "4g", "up": True, "signal_dbm": -75, "operator": "fake", "tech": "LTE",
                  "ip": "10.0.0.2", "dialups": 1, "modem_resets": 0},
            cloud={"connects": 1, "events_queued": 0, "telemetry_queued": 0, "events_dropped": 0,
                   "telemetry_dropped": 0},
            memory={"heap_free": 150000, "heap_min": 120000, "heap_max_block": 90000},
            nodes=[], alarms=[])

    def _uptime(self) -> int:
        return int(time.monotonic() - self._started)


def _status(status: str, gateway: str) -> str:
    return json.dumps([{"bn": f"{gateway}:", "n": "status", "vs": status}])


def _zone_of(target: Any) -> int | None:
    if isinstance(target, str) and target.startswith("zone:") and target[5:].isdigit():
        return int(target[5:])
    return {"actuator-node-01": 1}.get(target)


READINGS = ("ph", "tds_ppm", "temp_c", "level_pct", "liters")


def _reading(item: str, parser: argparse.ArgumentParser) -> tuple[int, str, float]:
    """"1:tds_ppm=500" -> (1, "tds_ppm", 500.0)."""
    try:
        zone, rest = item.split(":", 1)
        key, value = rest.split("=", 1)
        if key not in READINGS:
            raise ValueError(key)
        return int(zone), key, float(value)
    except ValueError:
        parser.error(f"--set {item}: give ZONE:KEY=VALUE, KEY one of {', '.join(READINGS)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="A stand-in for the prototype rig's gateway.")
    parser.add_argument("--zones", type=int, nargs="+", default=[1])
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between updates")
    parser.add_argument("--time-scale", type=float, default=1.0, help="run doses this much faster")
    readings = ", ".join(READINGS)
    parser.add_argument("--set", action="append", default=[], metavar="ZONE:KEY=VALUE",
                        help=f"a zone's starting reading, e.g. 1:tds_ppm=500 ({readings})")
    args = parser.parse_args(argv)
    start = [_reading(item, parser) for item in args.set]
    load_dotenv(SIM_ROOT / ".env")
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # A new boot on every start, as the gateway's own count rises on each power-on: the service
    # drops a repeated (gateway, boot, schema, seq), so a restart with boot 1 and seq 1 again would
    # be ignored as old until its seq passed the last run's.
    fake = FakeRig(load_settings(), load_rig_settings(), zones=args.zones, boot=int(time.time()),
                   interval_s=args.interval, time_scale=args.time_scale)
    for zone, key, value in start:
        if zone not in fake.zones:
            parser.error(f"--set {zone}:{key}: zone {zone} isn't in --zones")
        setattr(fake.zones[zone], key, value)
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    try:
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            runner.run(fake.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
