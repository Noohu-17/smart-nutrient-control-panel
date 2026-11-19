"""What the back end refuses or switches off on a server, and what stays easy in dev."""

import importlib

import pytest

import app.main
from app.config import DEV_DB_PASSWORD, DEV_SESSION_SECRET, load_settings

SERVER = {"APP_ENV": "staging", "DB_PASSWORD": "a-real-password", "SESSION_SECRET": "a-real-session-secret-of-at-least-32-chars"}


def test_dev_falls_back_to_the_dev_password_and_shows_the_docs():
    settings = load_settings({})
    assert settings.db_password == DEV_DB_PASSWORD
    assert settings.api_docs_enabled is True
    assert (settings.mqtt_username, settings.mqtt_password) == (None, None)


@pytest.mark.parametrize(
    "env",
    [{"APP_ENV": "staging"}, SERVER | {"DB_PASSWORD": ""}, SERVER | {"DB_PASSWORD": DEV_DB_PASSWORD}],
    ids=["missing", "blank", "dev password"],
)
def test_a_server_refuses_to_start_without_a_real_database_password(env):
    with pytest.raises(ValueError, match="DB_PASSWORD"):
        load_settings(env)


def test_a_server_uses_the_password_it_is_given():
    assert load_settings(SERVER).db_password == "a-real-password"


def test_dev_falls_back_to_the_dev_session_secret_and_12_hour_sessions():
    settings = load_settings({})
    assert (settings.session_secret, settings.session_hours) == (DEV_SESSION_SECRET, 12.0)


@pytest.mark.parametrize(
    "secret",
    [None, "", DEV_SESSION_SECRET, "x" * 31],
    ids=["missing", "blank", "dev secret", "too short"],
)
def test_a_server_refuses_to_start_without_a_real_session_secret(secret):
    env = {key: value for key, value in SERVER.items() if key != "SESSION_SECRET"}
    if secret is not None:
        env["SESSION_SECRET"] = secret
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        load_settings(env)


def test_a_server_uses_the_session_secret_and_hours_it_is_given():
    settings = load_settings(SERVER | {"SESSION_HOURS": "8"})
    assert (settings.session_secret, settings.session_hours) == ("a-real-session-secret-of-at-least-32-chars", 8.0)


@pytest.mark.parametrize(
    ("env", "enabled"),
    [
        (SERVER, False),
        (SERVER | {"API_DOCS_ENABLED": "false"}, False),
        (SERVER | {"API_DOCS_ENABLED": "true"}, True),
        ({"API_DOCS_ENABLED": "false"}, False),
    ],
)
def test_the_docs_setting(env, enabled):
    assert load_settings(env).api_docs_enabled is enabled


@pytest.fixture
def reload_main(monkeypatch):
    """app.main with the given environment; the test's changes are undone afterwards."""

    def reload(**env):
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(app.main).app

    yield reload
    monkeypatch.undo()
    importlib.reload(app.main)


def _paths(fastapi_app) -> set[str]:
    return {route.path for route in fastapi_app.routes}


def test_the_docs_routes_are_gone_on_a_server(reload_main):
    paths = _paths(reload_main(**SERVER))
    assert "/api/health" in paths
    assert not paths & {"/api/docs", "/api/openapi.json"}


def test_the_docs_routes_are_there_in_dev(reload_main):
    assert {"/api/docs", "/api/openapi.json"} <= _paths(reload_main(APP_ENV="dev"))


def test_the_api_will_not_import_on_a_server_without_a_password(monkeypatch, reload_main):
    monkeypatch.delenv("DB_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="DB_PASSWORD"):
        reload_main(APP_ENV="staging")
