"""The simulator's control API over HTTP (T4-9): routes, answers and contract 11.1 errors.

A real server on a free port of 127.0.0.1, asked with raw HTTP as the back end would.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from sim.clock import SimClock
from sim.config import load_speed_options
from sim.control_api import MAX_BODY_BYTES, serve
from sim.farm import load_farm
from sim.playground import Playground
from sim.settings import load_settings

CLIENT_A = load_settings({}).client_dir
SIM_START = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)


@pytest.fixture
def playground():
    clock = SimClock(SIM_START, 60, load_speed_options(CLIENT_A), monotonic=lambda: 0.0)
    farm = load_farm(CLIENT_A)
    farm.now = SIM_START
    return Playground(farm, clock, CLIENT_A, device_ids=["ZC1"], min_real_reading_s=5)


async def request(port, method, path, body=None, *, raw=None, length=None):
    data = raw if raw is not None else b"" if body is None else json.dumps(body).encode()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        f"{method} {path} HTTP/1.1\r\nHost: simulator\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(data) if length is None else length}\r\n\r\n".encode() + data
    )
    await writer.drain()
    response = await reader.read()  # the server closes after one answer
    writer.close()
    head, _, payload = response.partition(b"\r\n\r\n")
    status_line, *header_lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in header_lines)
    assert headers["Content-Type"] == "application/json"
    assert int(headers["Content-Length"]) == len(payload)
    return int(status_line.split()[1]), headers, json.loads(payload)


def call(playground, *requests, **options):
    """Answers to each (method, path[, body]) in turn, from one server."""

    async def go():
        server = await serve(playground, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return [await request(port, *r, **options) for r in requests]

    return asyncio.run(go())


def test_get_state(playground):
    [(status, headers, body)] = call(playground, ("GET", "/state"))
    assert status == 200 and headers["Connection"] == "close"
    assert body == playground.state()


def test_each_change_answers_with_the_new_state(playground):
    answers = call(
        playground,
        ("POST", "/speed", {"x": 0}),
        ("POST", "/holds", {"target": "Z1", "metric": "reservoirPct", "value": 15}),
        ("POST", "/faults", {"kind": "SENSOR_OFFLINE", "target": "Z1.ph", "on": True}),
        ("DELETE", "/holds/Z1/reservoirPct"),
        ("POST", "/holds", {"target": "Z1", "metric": "dayTankPct.ACID", "value": 5}),
        ("POST", "/holds/release-all"),
        ("POST", "/reset"),
    )
    assert [status for status, _, _ in answers] == [200] * 7
    speed, held, drill, released, tank, released_all, reset = (b for _, _, b in answers)
    assert speed["speed"] == 0
    assert held["holds"] == [
        {"target": "Z1", "metric": "reservoirPct", "value": 15, "gradual": True}]
    assert drill["faults"] == [{"kind": "SENSOR_OFFLINE", "target": "Z1.ph"}]
    assert released["holds"] == [] and tank["holds"][0]["metric"] == "dayTankPct.ACID"
    assert released_all["holds"] == []
    assert (reset["speed"], reset["faults"]) == (60, [])


def test_an_out_of_range_value_is_refused_with_422_and_the_field(playground):
    [(status, _, body)] = call(
        playground, ("POST", "/holds", {"target": "Z1", "metric": "ph", "value": 15})
    )
    assert status == 422
    assert body == {"error": {
        "code": "VALIDATION_ERROR",
        "message": "ph must be a number from 0 to 14",
        "details": {"fields": [{"field": "body.value",
                                "message": "ph must be a number from 0 to 14"}]},
    }}


def test_releasing_what_is_not_held_is_404(playground):
    [(status, _, body)] = call(playground, ("DELETE", "/holds/Z1/ph"))
    assert (status, body["error"]["code"]) == (404, "NOT_FOUND")


def test_a_held_day_tank_can_be_released_by_its_dotted_name(playground):
    playground.hold({"target": "Z1", "metric": "dayTankPct.A", "value": 10})
    [(status, _, body)] = call(playground, ("DELETE", "/holds/Z1/dayTankPct.A"))
    assert (status, body["holds"]) == (200, [])


def test_an_unknown_path_is_404_and_a_wrong_method_is_405_with_allow(playground):
    (missing, _, body), (wrong, headers, _) = call(
        playground, ("GET", "/weather"), ("GET", "/reset")
    )
    assert (missing, body["error"]["code"]) == (404, "NOT_FOUND")
    assert (wrong, headers["Allow"]) == (405, "POST")


@pytest.mark.parametrize("raw", [b"{speed: 60", b""])
def test_a_body_that_is_not_a_json_object_is_refused(playground, raw):
    [(status, _, body)] = call(playground, ("POST", "/speed"), raw=raw)
    assert (status, body["error"]["details"]["fields"][0]["field"]) == (422, "body")
    assert playground.clock.speed == 60


def test_a_body_too_big_is_refused_unread(playground):
    [(status, _, body)] = call(playground, ("POST", "/holds"), length=MAX_BODY_BYTES + 1)
    assert (status, body["error"]["code"]) == (413, "VALIDATION_ERROR")


def test_a_failure_inside_is_500_with_no_detail(playground, monkeypatch):
    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(playground, "reset", broken)
    [(status, _, body)] = call(playground, ("POST", "/reset"))
    assert status == 500
    assert body["error"] == {
        "code": "INTERNAL", "message": "Something went wrong in the simulator", "details": {}}
