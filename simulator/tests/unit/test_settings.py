"""Simulator settings from the environment (T4-1)."""

from __future__ import annotations

import pytest

from sim.settings import load_settings


def test_defaults_match_the_dev_broker():
    s = load_settings({})
    assert (s.mqtt_host, s.mqtt_port, s.log_level) == ("localhost", 1883, "INFO")


def test_environment_overrides_defaults():
    settings = load_settings({"MQTT_HOST": "mqtt", "MQTT_PORT": "1884", "LOG_LEVEL": "debug"})
    assert (settings.mqtt_host, settings.mqtt_port, settings.log_level) == ("mqtt", 1884, "DEBUG")


@pytest.mark.parametrize("port", ["abc", "0", "70000", "1.5"])
def test_rejects_bad_ports(port):
    with pytest.raises(ValueError, match="MQTT_PORT"):
        load_settings({"MQTT_PORT": port})


def test_one_login_per_device_and_anonymous_without_password():
    settings = load_settings({"MQTT_PASSWORD_ZC1": "secret", "MQTT_PASSWORD_ZC2": ""})
    assert settings.login("ZC1") == ("ZC1", "secret")
    assert settings.login("ZC2") == (None, None)
    assert settings.login("GW1") == (None, None)


def test_passwords_never_appear_in_repr():
    assert "secret" not in repr(load_settings({"MQTT_PASSWORD_ZC1": "secret"}))
