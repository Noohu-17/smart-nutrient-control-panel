"""The command publisher against a real broker (T1-17).

Start one with: docker compose -f infra/docker-compose.dev.yml up -d mqtt
Without a broker on MQTT_HOST:MQTT_PORT these tests are skipped. They use a site and client IDs
of their own, so they never take over a running API's CLOUD-api session or reach a simulator.
"""

import dataclasses
import json
import socket
import threading
import time
import uuid
from datetime import UTC, datetime

import paho.mqtt.client as mqtt
import pytest

from app.api.commands import messages
from app.api.commands.publish import MqttPublisher, NotSent
from app.config import load_settings

TIMEOUT_S = 10


@pytest.fixture
def settings():
    settings = load_settings()
    try:
        socket.create_connection((settings.mqtt_host, settings.mqtt_port), timeout=1).close()
    except OSError:
        pytest.skip(f"no MQTT broker on {settings.mqtt_host}:{settings.mqtt_port}")
    return settings


@pytest.fixture
def site():
    return f"test-{uuid.uuid4().hex[:8]}"


def publisher(settings):
    return MqttPublisher(settings, client_id=f"CLOUD-api-test-{uuid.uuid4().hex[:8]}")


class Listener:
    """A client subscribed to one topic, collecting what arrives."""

    def __init__(self, settings, topic):
        self.messages: list[mqtt.MQTTMessage] = []
        self.arrived = threading.Event()
        subscribed = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"test-listener-{uuid.uuid4().hex[:8]}")
        if settings.mqtt_username:
            self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self.client.on_connect = lambda client, *_: client.subscribe(topic, qos=1)
        self.client.on_subscribe = lambda *_: subscribed.set()
        self.client.on_message = lambda _client, _userdata, message: (self.messages.append(message), self.arrived.set())
        self.client.connect(settings.mqtt_host, settings.mqtt_port)
        self.client.loop_start()
        assert subscribed.wait(TIMEOUT_S), "the listener could not subscribe"

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


def test_a_stop_reaches_a_subscriber_at_qos_1_and_isnt_retained(settings, site):
    command = messages.stop(site, "ALL_OPERATIONS", None, datetime.now(UTC))
    listener = Listener(settings, command.topic)
    try:
        publisher(settings).publish(command.topic, command.body)
        assert listener.arrived.wait(TIMEOUT_S), "the stop never arrived"
    finally:
        listener.close()
    [message] = listener.messages
    assert message.topic == f"farm/{site}/stop"
    assert json.loads(message.payload) == command.body
    assert message.qos == 1

    late = Listener(settings, command.topic)  # subscribes after it was sent
    try:
        time.sleep(0.5)
        assert late.messages == []  # never retained (§4): a controller that reconnects won't replay it
    finally:
        late.close()


def test_a_resume_goes_to_its_zones_command_topic(settings, site):
    command = messages.resume_zone(site, "Z1", datetime.now(UTC))
    listener = Listener(settings, f"farm/{site}/zone/+/cmd")
    try:
        publisher(settings).publish(command.topic, command.body)
        assert listener.arrived.wait(TIMEOUT_S)
    finally:
        listener.close()
    assert listener.messages[0].topic == f"farm/{site}/zone/Z1/cmd"


def test_commands_sent_at_the_same_time_all_arrive(settings, site):
    """One publisher sends one at a time, so one connection never takes over another's."""
    commands = [messages.stop(site, "ZONE", f"Z{n}", datetime.now(UTC)) for n in range(1, 6)]
    listener = Listener(settings, f"farm/{site}/stop")
    shared = publisher(settings)
    errors = []

    def send(command):
        try:
            shared.publish(command.topic, command.body)
        except NotSent as exc:
            errors.append(exc)

    threads = [threading.Thread(target=send, args=(c,)) for c in commands]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(TIMEOUT_S)
        deadline = time.monotonic() + TIMEOUT_S
        while len(listener.messages) < len(commands) and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        listener.close()
    assert errors == []
    assert sorted(json.loads(m.payload)["cmdId"] for m in listener.messages) == sorted(c.cmd_id for c in commands)


def test_no_broker_is_not_sent_and_says_so_quickly(settings, site):
    nowhere = dataclasses.replace(settings, mqtt_host="127.0.0.1", mqtt_port=free_port())
    command = messages.stop(site, "ALL_OPERATIONS", None, datetime.now(UTC))
    started = time.monotonic()
    with pytest.raises(NotSent, match="cannot reach the broker"):
        MqttPublisher(nowhere, timeout_s=2).publish(command.topic, command.body)
    assert time.monotonic() - started < 5


def free_port() -> int:
    """A local port nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]

