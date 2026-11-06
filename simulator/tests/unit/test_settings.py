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


def test_the_control_api_listens_on_this_machine_only_unless_told_otherwise():
    s = load_settings({})
    assert (s.control_host, s.control_port) == ("127.0.0.1", 8100)
    s = load_settings({"SIM_CONTROL_HOST": "0.0.0.0", "SIM_CONTROL_PORT": "8200"})
    assert (s.control_host, s.control_port) == ("0.0.0.0", 8200)


@pytest.mark.parametrize("port", ["abc", "0", "70000"])
def test_rejects_a_bad_control_port(port):
    with pytest.raises(ValueError, match="SIM_CONTROL_PORT"):
        load_settings({"SIM_CONTROL_PORT": port})


def test_one_login_per_device_and_anonymous_without_password():
    settings = load_settings({"MQTT_PASSWORD_ZC1": "secret", "MQTT_PASSWORD_ZC2": ""})
    assert settings.login("ZC1") == ("ZC1", "secret")
    assert settings.login("ZC2") == (None, None)
    assert settings.login("GW1") == (None, None)


def test_passwords_never_appear_in_repr():
    assert "secret" not in repr(load_settings({"MQTT_PASSWORD_ZC1": "secret"}))
