"""Sending a command to the farm as CLOUD-api (contract §3, §4).

One connection per command: connect, publish at QoS 1, never retained, wait for the broker's
PUBACK, disconnect. DEVICE_CONFIG (§7.7) goes the same way, retained (app.api.device_config). A command the broker hasn't confirmed is dropped with its connection, so it
is never sent later, after the person was told it failed.
"""

import json
import logging
import threading
from functools import cache
from typing import Any, Protocol

import paho.mqtt.client as mqtt

from ...config import Settings

log = logging.getLogger("api")

CLIENT_ID = "CLOUD-api"  # contract §3: the API's one MQTT client ID
TIMEOUT_S = 5.0  # for each of: connecting, and the broker confirming the command


class NotSent(Exception):
    """The broker didn't confirm the command, so it may not reach the farm."""


class Publisher(Protocol):
    def publish(self, topic: str, body: dict[str, Any], *, retain: bool = False) -> None:
        """Send one command; raise NotSent unless the broker has confirmed it. Only DEVICE_CONFIG
        is retained (§4, §7.7)."""


class MqttPublisher:
    def __init__(self, settings: Settings, *, client_id: str = CLIENT_ID, timeout_s: float = TIMEOUT_S):
        self._settings, self._client_id, self._timeout_s = settings, client_id, timeout_s
        # One command at a time: a second connection with the same client ID would take over
        # the first one's session, and its command could be lost.
        self._lock = threading.Lock()

    def publish(self, topic: str, body: dict[str, Any], *, retain: bool = False) -> None:
        payload = json.dumps(body).encode()
        with self._lock:
            self._publish(topic, payload, retain)

    def _publish(self, topic: str, payload: bytes, retain: bool = False) -> None:
        settings = self._settings
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            clean_session=False,  # contract §3: the back end uses persistent sessions
            protocol=mqtt.MQTTv311,
        )
        if settings.mqtt_username:
            client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        client.connect_timeout = self._timeout_s
        connected = threading.Event()
        refused: list[str] = []

        def on_connect(_client: Any, _userdata: Any, _flags: Any, reason: Any, _properties: Any) -> None:
            if reason.is_failure:
                refused.append(str(reason))
            connected.set()

        client.on_connect = on_connect
        try:
            client.connect(settings.mqtt_host, settings.mqtt_port)
        except (OSError, ValueError) as exc:  # refused, unreachable, timed out, or a bad host name
            raise NotSent(f"cannot reach the broker: {exc}") from exc
        client.loop_start()
        try:
            if not connected.wait(self._timeout_s):
                raise NotSent("the broker did not answer the connection")
            if refused:
                raise NotSent(f"the broker refused the connection: {refused[0]}")
            sent = client.publish(topic, payload, qos=1, retain=retain)  # commands never are (§4)
            sent.wait_for_publish(self._timeout_s)
            if not sent.is_published():
                raise NotSent("the broker did not confirm the command in time")
        except (RuntimeError, ValueError) as exc:  # wait_for_publish: the connection went away
            raise NotSent(f"the connection to the broker failed: {exc}") from exc
        finally:
            client.disconnect()
            client.loop_stop()


@cache
def publisher_for(settings: Settings) -> MqttPublisher:
    """One publisher, and so one lock, per process."""
    return MqttPublisher(settings)
