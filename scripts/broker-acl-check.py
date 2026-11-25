"""Broker check (T1-36): the staging broker's TLS port and the gateway's permissions.

    backend/.venv/Scripts/python scripts/broker-acl-check.py --env-file infra/.env.staging

Run it against a staging broker on this machine, never on a site in use: it publishes test
messages, the forbidden ones included, on the farm's topics. Steps: infra/readme.md.

    ports        the broker container publishes 8883 and not 1883
    trust        the broker's certificate is trusted through the project's CA and nothing else
    logins       on 8883: anonymous, a wrong password, CLOUD-worker and ZC1 with the simulator's
                 password are refused; GW1 logs in with its add-device-login.sh password
    publish      everything GW1 may publish (acl.conf) arrives; the forbidden topics never do
    size         a message over the broker's 64 KB packet limit (mosquitto.conf) never arrives
    receive      subscribed to all of farm/siet-ph1/#, GW1 is given its read topics and nothing else

The broker acknowledges a publish its ACL drops (MQTT 3.1.1 can't refuse one), so arrival is
checked by a CLOUD-worker subscriber on 1883, run inside the broker's container. It also accepts
any subscription and filters each message by the reader's ACL, so what GW1 may read is checked by
what CLOUD-api sends it.

Passwords come from the environment, or from --env-file (the environment wins):
MQTT_PASSWORD_GW1, MQTT_PASSWORD_ZC1, MQTT_PASSWORD_CLOUD_WORKER and MQTT_PASSWORD_CLOUD_API.
8883 takes only the logins add-device-login.sh made, so set MQTT_PASSWORD_GW1 in the environment
to the password it printed for GW1. MQTT_PASSWORD_ZC1 is the simulator's, from infra/.env.staging.
Needs paho-mqtt (the backend's virtual environment has it) and docker.
Exits with 0 when every check passed, 1 when one failed, 2 when it could not start.
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
ACL = ROOT / "infra" / "mosquitto" / "staging" / "acl.conf"
CA = ROOT / "infra" / "mosquitto" / "ca" / "ca.crt"
SITE = "siet-ph1"
PROJECT = "sna-staging"  # infra/docker-compose.staging.yml "name"
WAIT_S = 5.0
SETTLE_S = 3.0  # how long a dropped message is given to turn up anyway
MAX_PACKET = 65536  # mosquitto.conf max_packet_size

# Topics GW1 must not publish to, each one next to something it may do.
FORBIDDEN = [
    f"farm/{SITE}/zone/Z3/telemetry",  # a zone that isn't in the layout
    f"farm/{SITE}/zone/Z1/cmd",  # a command: the cloud's alone
    f"farm/{SITE}/climate/cmd",
    f"farm/{SITE}/stop",
    f"farm/{SITE}/device/ZC1/config",  # config: the cloud's alone
    f"farm/{SITE}/device/CLOUD-api/status",  # not a farm device
    f"farm/{SITE}/device/ZC9/status",  # a device that isn't in the layout
    f"farm/{SITE}/gateway/journal/extra",
]


def read_env_file(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def acl_topics(user: str) -> tuple[list[str], list[str]]:
    """(write, read) topics of one account in acl.conf, in file order."""
    writes, reads, current = [], [], None
    for line in ACL.read_text(encoding="utf-8").splitlines():
        words = line.split()
        if len(words) == 2 and words[0] == "user":
            current = words[1]
        elif current == user and len(words) == 3 and words[0] == "topic":
            (writes if words[1] == "write" else reads).append(words[2])
    return writes, reads


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def check(self, ok: bool, text: str) -> None:
        print(f"{'PASS' if ok else 'FAIL'}  {text}", flush=True)
        self.failed += not ok

    def info(self, text: str) -> None:
        print(f"INFO  {text}", flush=True)


def connect(host: str, port: int, user: str | None, password: str | None, *,
            ca: Path | None) -> tuple[mqtt.Client | None, str]:
    """A logged-in client, or None and why not. ca None: the system's trusted CAs."""
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id=f"acl-check-{secrets.token_hex(4)}", protocol=mqtt.MQTTv311)
    client.tls_set(ca_certs=str(ca) if ca else None)
    if user:
        client.username_pw_set(user, password)
    answered, outcome = threading.Event(), []

    def on_connect(_c, _u, _flags, reason, _props) -> None:
        outcome.append(reason)
        answered.set()

    client.on_connect = on_connect
    try:
        client.connect(host, port, keepalive=30)
    except OSError as exc:  # ssl errors included
        return None, f"{type(exc).__name__}: {exc}"
    client.loop_start()
    if not answered.wait(WAIT_S) or outcome[0].is_failure:
        why = str(outcome[0]) if outcome else "no answer"
        client.disconnect()
        client.loop_stop()
        return None, why
    return client, "connected"


class Watcher:
    """CLOUD-worker on 1883 inside the broker's container: what really reached subscribers."""

    def __init__(self, container: str, password: str) -> None:
        self.seen: dict[str, str] = {}  # payload -> topic
        self._new = threading.Condition()
        subscribe = ('exec mosquitto_sub -h localhost -p 1883 -u CLOUD-worker -P "$SUB_PASSWORD"'
                     f' -t "farm/{SITE}/#" -v -W 120')
        self._proc = subprocess.Popen(
            ["docker", "exec", "-e", "SUB_PASSWORD", container, "sh", "-c", subscribe],
            env={**os.environ, "SUB_PASSWORD": password},
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self._proc.stdout:
            topic, _, payload = line.strip().partition(" ")
            with self._new:
                self.seen[payload] = topic
                self._new.notify_all()

    def wait_for(self, payload: str, timeout: float) -> bool:
        with self._new:
            return self._new.wait_for(lambda: payload in self.seen, timeout)

    def stop(self) -> None:
        self._proc.terminate()


def mqtt_container() -> str:
    found = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}",
         "--filter", "label=com.docker.compose.service=mqtt"],
        capture_output=True, text=True, check=False,
    ).stdout.split()
    return found[0] if found else ""


def check_ports(report: Report, container: str) -> None:
    ports = subprocess.run(["docker", "port", container], capture_output=True, text=True,
                           check=False).stdout
    report.check("8883/tcp" in ports, "the broker publishes 8883")
    report.check("1883/tcp" not in ports, "the broker does not publish 1883")


def check_publish(report: Report, gw1: mqtt.Client, watcher: Watcher) -> None:
    run = secrets.token_hex(4)
    warm_up = f"acl-check-{run}-ready"
    for _ in range(5):  # until the subscriber is listening
        gw1.publish(f"farm/{SITE}/device/GW1/status", warm_up, qos=1).wait_for_publish(WAIT_S)
        if watcher.wait_for(warm_up, 2):
            break
    else:
        report.check(False, "the CLOUD-worker subscriber hears GW1 (nothing else can be checked)")
        return

    allowed, _ = acl_topics("GW1")
    sent = {}
    for kind, topics in (("allowed", allowed), ("forbidden", FORBIDDEN)):
        for i, topic in enumerate(topics):
            payload = f"acl-check-{run}-{kind}-{i}"
            gw1.publish(topic, payload, qos=1).wait_for_publish(WAIT_S)
            sent[payload] = (kind, topic)
    time.sleep(SETTLE_S)
    for payload, (kind, topic) in sent.items():
        arrived = payload in watcher.seen
        if kind == "allowed":
            report.check(arrived, f"GW1 may publish   {topic}")
        else:
            report.check(not arrived, f"GW1 may not publish {topic}")


def check_packet_size(report: Report, host: str, port: int, password: str, ca: Path,
                      watcher: Watcher) -> None:
    """A second GW1 connection sends one message over the limit on a topic it may publish.
    The broker disconnects it, so the message must never reach the subscriber."""
    client, why = connect(host, port, "GW1", password, ca=ca)
    if not client:
        report.check(False, f"GW1 logs in again for the size check ({why})")
        return
    run = secrets.token_hex(4)
    payload = f"acl-check-{run}-oversize-".ljust(MAX_PACKET + 1024, "x")
    client.publish(f"farm/{SITE}/device/GW1/status", payload, qos=1)
    arrived = watcher.wait_for(payload, SETTLE_S)
    client.disconnect()
    client.loop_stop()
    report.check(not arrived, f"8883 refuses a message over {MAX_PACKET // 1024} KB")


def cloud_publish(container: str, password: str, messages: list[tuple[str, str]]) -> None:
    """CLOUD-api publishes (topic, payload) pairs on 1883, inside the broker's container."""
    script = ('for m in "$@"; do mosquitto_pub -h localhost -p 1883 -u CLOUD-api -P "$PUB_PASSWORD"'
              ' -q 1 -t "${m%% *}" -m "${m#* }"; done')
    subprocess.run(
        ["docker", "exec", "-e", "PUB_PASSWORD", container, "sh", "-c", script, "sh",
         *(f"{topic} {payload}" for topic, payload in messages)],
        env={**os.environ, "PUB_PASSWORD": password}, check=False, capture_output=True,
    )


def check_receive(report: Report, gw1: mqtt.Client, container: str, cloud_password: str) -> None:
    """What GW1 is given. The broker accepts any subscription and filters each message by
    GW1's read topics, so a wide subscription must still bring only those."""
    answers: dict[int, list] = {}
    received: dict[str, str] = {}  # payload -> topic
    changed = threading.Condition()

    def on_subscribe(_c, _u, mid, reason_codes, _props) -> None:
        with changed:
            answers[mid] = reason_codes
            changed.notify_all()

    def on_message(_c, _u, message) -> None:
        with changed:
            received[message.payload.decode(errors="replace")] = message.topic

    gw1.on_subscribe, gw1.on_message = on_subscribe, on_message

    def granted(topic: str) -> bool:
        _, mid = gw1.subscribe(topic, qos=1)
        with changed:
            changed.wait_for(lambda: mid in answers, WAIT_S)
        return mid in answers and not answers[mid][0].is_failure

    _, reads = acl_topics("GW1")
    for topic in reads:
        report.check(granted(topic), f"GW1 may subscribe {topic}")
    wide = f"farm/{SITE}/#"
    report.info(f"GW1 subscribing {wide}: {'accepted' if granted(wide) else 'refused'}; "
                "what it is given is checked next")

    run = secrets.token_hex(4)
    sent = [(topic, f"acl-check-{run}-read-{i}", True) for i, topic in enumerate(reads)]
    sent += [(topic, f"acl-check-{run}-noread-{i}", False) for i, topic in enumerate([
        f"farm/{SITE}/zone/Z3/cmd",  # a zone that isn't in the layout
        f"farm/{SITE}/device/PM1/config",  # PM1 reads nothing (contract 4)
        f"farm/{SITE}/device/GW1/config",  # not in the contract
    ])]
    cloud_publish(container, cloud_password, [(topic, payload) for topic, payload, _ in sent])
    own = f"acl-check-{run}-own"
    gw1.publish(f"farm/{SITE}/zone/Z1/telemetry", own, qos=1).wait_for_publish(WAIT_S)
    time.sleep(SETTLE_S)
    for topic, payload, may_read in sent:
        if may_read:
            report.check(payload in received, f"GW1 is given     {topic}")
        else:
            report.check(payload not in received, f"GW1 is not given {topic}")
    report.check(own not in received,
                 f"GW1 is not given farm/{SITE}/zone/Z1/telemetry (it may publish it, not read it)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1", help="the broker's address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8883, help="its TLS port (default 8883)")
    parser.add_argument("--ca", type=Path, default=CA, help="the project CA's certificate")
    parser.add_argument("--env-file", type=Path, help="read the passwords from this file too")
    args = parser.parse_args()

    env = {**(read_env_file(args.env_file) if args.env_file else {}), **os.environ}
    names = ("MQTT_PASSWORD_GW1", "MQTT_PASSWORD_ZC1", "MQTT_PASSWORD_CLOUD_WORKER",
             "MQTT_PASSWORD_CLOUD_API")
    missing = [name for name in names if not env.get(name)]
    if missing:
        print(f"broker-acl-check: set {', '.join(missing)}", file=sys.stderr)
        return 2
    gw1_password, zc1_password, worker_password, api_password = (env[name] for name in names)
    if not args.ca.is_file():
        print(f"broker-acl-check: no CA certificate at {args.ca}", file=sys.stderr)
        return 2
    container = mqtt_container()
    if not container:
        print(f"broker-acl-check: no running mqtt container in the {PROJECT} project", file=sys.stderr)
        return 2

    report = Report()
    check_ports(report, container)

    client, why = connect(args.host, args.port, "GW1", gw1_password, ca=None)
    report.check(client is None and "CERTIFICATE_VERIFY_FAILED" in why,
                 f"without the project's CA the certificate is not trusted ({why})")
    if client:
        client.disconnect()

    for user, password, what in ((None, None, "anonymous"),
                                 ("GW1", "not-" + gw1_password, "GW1 with a wrong password"),
                                 ("CLOUD-worker", worker_password, "CLOUD-worker (back end only)"),
                                 ("ZC1", zc1_password, "ZC1 with the simulator's password")):
        client, why = connect(args.host, args.port, user, password, ca=args.ca)
        report.check(client is None, f"8883 refuses {what} ({why})")
        if client:
            client.disconnect()

    gw1, why = connect(args.host, args.port, "GW1", gw1_password, ca=args.ca)
    report.check(gw1 is not None, f"GW1 logs in on 8883 over TLS ({why})"
                 + ("" if gw1 else ": is MQTT_PASSWORD_GW1 the password add-device-login.sh printed?"))
    if gw1:
        watcher = Watcher(container, worker_password)
        try:
            check_publish(report, gw1, watcher)
            check_packet_size(report, args.host, args.port, gw1_password, args.ca, watcher)
            check_receive(report, gw1, container, api_password)
        finally:
            watcher.stop()
            gw1.disconnect()
            gw1.loop_stop()

    print("broker-acl-check:", "all passed" if not report.failed else f"{report.failed} failed")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
