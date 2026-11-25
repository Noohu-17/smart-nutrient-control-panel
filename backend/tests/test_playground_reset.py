"""POST /api/sim/reset clears the 24-hour totals (T1-25, contract §11.4).

The simulator's controllers forget their totals on a reset, so the server must too, or the dose
review would block doses the farm itself would take. The simulator is test_sim_forward's fake;
doses reach the database through the worker, as in test_dose_requests.
"""

import dataclasses
import json
import logging
import time
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select
from test_dose_requests import (
    ORG,
    SITE,
    check,
    cmd_state,
    handle,
    ready_zone,
    review,
    write_dosing_pack,
)
from test_sim_forward import NO_DATABASE, FakeSimulator

from app.api.routes import get_engine, get_settings
from app.api.schemas import contract_time
from app.config import load_settings
from app.db.models import PlaygroundReset
from app.db.seed import seed
from app.main import app
from app.worker.main import Worker
from conftest import sign_in_as

CMD_TOPIC = "farm/siet-ph1/zone/Z1/cmd/state"
BLOCKED = ("BLOCK", "Line A would reach 1520 mL of the 1500 mL 24 h limit")  # 1400 + the review's 120


@pytest.fixture(autouse=True)
def forget_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def fake():
    simulator = FakeSimulator()
    yield simulator
    simulator.close()


def api_for(pack, engine, url) -> TestClient:
    settings = dataclasses.replace(load_settings(), client_dir=pack, sim_control_url=url)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_engine] = lambda: engine
    sign_in_as(app)  # an Owner: only an Owner may reset (§11.4)
    return TestClient(app, raise_server_exceptions=False)


def playground(tmp_path, migrated_db, features=None):
    pack = write_dosing_pack(tmp_path / ORG, features=features)
    seed(migrated_db, pack)
    worker = Worker(migrated_db, ORG, SITE)
    ready_zone(worker)
    return pack, worker


def dosed_just_now(worker, ml: float) -> None:
    """A dose reported now, after any reset this test has made."""
    time.sleep(0.01)
    handle(worker, cmd_state({"A": ml}, ts=contract_time(datetime.now(UTC)), cmd_id=str(uuid.uuid4()), seq=200), CMD_TOPIC)


def resets(engine) -> list[datetime]:
    with engine.connect() as connection:
        return list(connection.scalars(select(PlaygroundReset.reset_at)))


def test_a_reset_clears_the_24_hour_totals(tmp_path, migrated_db, fake):
    pack, worker = playground(tmp_path, migrated_db)
    api = api_for(pack, migrated_db, fake.url)
    handle(worker, cmd_state({"A": 1400}), CMD_TOPIC)
    assert check(review(api), "LIMIT_24H") == BLOCKED

    assert api.post("/api/sim/reset").status_code == 200
    assert check(review(api), "LIMIT_24H")[0] == "PASS"


def test_doses_after_the_reset_count_again(tmp_path, migrated_db, fake):
    pack, worker = playground(tmp_path, migrated_db)
    api = api_for(pack, migrated_db, fake.url)
    api.post("/api/sim/reset")
    dosed_just_now(worker, 1400)
    assert check(review(api), "LIMIT_24H") == BLOCKED


def test_each_reset_moves_the_sites_one_record_on(tmp_path, migrated_db, fake):
    pack, _ = playground(tmp_path, migrated_db)
    api = api_for(pack, migrated_db, fake.url)
    api.post("/api/sim/reset")
    first = resets(migrated_db)
    time.sleep(0.01)
    api.post("/api/sim/reset")
    second = resets(migrated_db)
    assert len(first) == len(second) == 1 and second[0] > first[0]


def test_a_reset_the_simulator_refused_keeps_the_totals(tmp_path, migrated_db, fake):
    pack, worker = playground(tmp_path, migrated_db)
    api = api_for(pack, migrated_db, fake.url)
    handle(worker, cmd_state({"A": 1400}), CMD_TOPIC)
    refusal = {"error": {"code": "CONFLICT", "message": "Busy resetting"}}
    fake.reply = (409, json.dumps(refusal).encode(), {"Content-Type": "application/json"})
    assert api.post("/api/sim/reset").status_code == 409
    assert resets(migrated_db) == []
    assert check(review(api), "LIMIT_24H") == BLOCKED


def test_a_database_that_cant_record_it_doesnt_undo_the_reset(tmp_path, migrated_db, fake, caplog):
    pack, _ = playground(tmp_path, migrated_db)
    logger = logging.getLogger("api.sim")
    logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.INFO, logger="api.sim"):
            response = api_for(pack, NO_DATABASE, fake.url).post("/api/sim/reset")
    finally:
        logger.removeHandler(caplog.handler)
    assert response.status_code == 200 and [c["path"] for c in fake.calls] == ["/reset"]
    lines = [r.getMessage() for r in caplog.records if r.name == "api.sim"]
    assert any(line.startswith("reset not recorded, so the 24 h totals still count doses") for line in lines), lines


def test_a_real_farm_never_counts_from_a_reset(tmp_path, migrated_db, fake):
    pack, worker = playground(tmp_path, migrated_db, features={"playground": False, "demoFarm": False})
    with migrated_db.begin() as connection:
        connection.execute(insert(PlaygroundReset).values(org_id=ORG, site_id=SITE, reset_at=datetime.now(UTC)))
    handle(worker, cmd_state({"A": 1400}), CMD_TOPIC)  # reported a minute ago, before that row
    assert check(review(api_for(pack, migrated_db, fake.url)), "LIMIT_24H")[0] == "BLOCK"
