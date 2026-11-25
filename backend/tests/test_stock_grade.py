"""PUT /api/stocks/{line}/npk: a stock's N-P-K grade set in the app, over nutrients.json npk."""

import json

import pytest
from sqlalchemy import select
from test_dose_requests import limits, write_dosing_pack
from test_read_api import api_client, assert_error

from app.db.models import AuditLog
from app.db.seed import seed

ORG = "client_A"


@pytest.fixture
def dosing_pack(tmp_path, migrated_db):
    """client_A's dosing pack with Stock A's label grade, 15-0-0."""
    pack = write_dosing_pack(tmp_path / ORG)
    nutrients = json.loads((pack / "nutrients.json").read_text(encoding="utf-8"))
    nutrients["doseLines"]["A"]["npk"] = {"n": 15, "p": 0, "k": 0}
    (pack / "nutrients.json").write_text(json.dumps(nutrients), encoding="utf-8")
    seed(migrated_db, pack)
    return pack


def test_an_agronomist_sets_a_stocks_grade_and_every_zone_doses_it(dosing_pack, migrated_db):
    api = api_client(dosing_pack, migrated_db, role="AGRONOMIST")
    answer = api.put("/api/stocks/A/npk", json={"n": 24, "p": 0, "k": 0})
    assert answer.status_code == 200
    assert answer.json() == {"line": "A", "npk": {"n": 24, "p": 0, "k": 0}}
    assert limits(api)["A"]["npk"] == {"n": 24, "p": 0, "k": 0}
    assert limits(api, "Z2")["A"]["npk"] == {"n": 24, "p": 0, "k": 0}
    assert limits(api)["B"]["npk"] is None  # still the pack's: none
    # set again: the newest stands
    api.put("/api/stocks/B/npk", json={"n": 5, "p": 26, "k": 0})
    api.put("/api/stocks/A/npk", json={"n": 20, "p": 1.5, "k": 0})
    assert {line: entry["npk"] for line, entry in limits(api).items() if line != "ACID"} == {
        "A": {"n": 20, "p": 1.5, "k": 0}, "B": {"n": 5, "p": 26, "k": 0},
    }


def test_each_change_is_in_the_audit_log_with_what_it_was(dosing_pack, migrated_db):
    api = api_client(dosing_pack, migrated_db, role="OWNER")
    api.put("/api/stocks/A/npk", json={"n": 24, "p": 0, "k": 0})
    api.put("/api/stocks/A/npk", json={"n": 20, "p": 0, "k": 0})
    with migrated_db.connect() as connection:
        rows = connection.execute(
            select(AuditLog.role, AuditLog.action, AuditLog.target, AuditLog.detail).order_by(AuditLog.at)
        ).all()
    assert [tuple(row) for row in rows] == [
        ("OWNER", "STOCK_NPK_CHANGED", "A", {"from": {"n": 15, "p": 0, "k": 0}, "to": {"n": 24, "p": 0, "k": 0}}),
        ("OWNER", "STOCK_NPK_CHANGED", "A", {"from": {"n": 24, "p": 0, "k": 0}, "to": {"n": 20, "p": 0, "k": 0}}),
    ]


@pytest.mark.parametrize("role", ["OPERATOR", "VIEWER"])
def test_only_owners_and_agronomists_change_a_grade(dosing_pack, migrated_db, role):
    api = api_client(dosing_pack, migrated_db, role=role)
    answer = api.put("/api/stocks/A/npk", json={"n": 24, "p": 0, "k": 0})
    assert_error(answer, 403, "FORBIDDEN")
    assert answer.json()["error"]["message"] == "Only Owners and Agronomists change a stock's N-P-K"
    assert limits(api)["A"]["npk"] == {"n": 15, "p": 0, "k": 0}


@pytest.mark.parametrize(
    "body",
    [{"n": 24, "p": 0}, {"n": -1, "p": 0, "k": 0}, {"n": 101, "p": 0, "k": 0}, {"n": 60, "p": 30, "k": 20}],
    ids=["no k", "below 0", "over 100", "over 100 together"],
)
def test_a_grade_is_three_percentages_that_fit_in_100(dosing_pack, migrated_db, body):
    api = api_client(dosing_pack, migrated_db)
    assert_error(api.put("/api/stocks/A/npk", json=body), 422, "VALIDATION_ERROR")


def test_a_line_no_zone_doses_is_404(dosing_pack, migrated_db):
    api = api_client(dosing_pack, migrated_db)
    assert_error(api.put("/api/stocks/C/npk", json={"n": 1, "p": 1, "k": 1}), 404, "NOT_FOUND")
