"""Load a client's layout.json into the database: the site, its zones, benches and devices.

Run from backend/, after `alembic upgrade head`:
    python -m app.db.seed                            # the pack in CLIENT_DIR (default client_A)
    python -m app.db.seed ../config/clients/client_B

The database then matches the file: rows are added or updated, and zones, benches and
devices no longer in the file are removed. Running it twice changes nothing. Every value
comes from layout.json; org_id is the client folder name until the organisation layer
exists (OPEN-11). Check the pack first with scripts/validate_config.py.

On a server, app.db.config_store's import does this too, with the rest of the pack (T1-39).
"""

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, Engine, delete
from sqlalchemy.dialects.postgresql import insert

from ..config import load_settings
from .engine import create_db_engine
from .models import Base, Bench, Device, Site, Zone

Rows = dict[type[Base], list[dict[str, Any]]]


def layout_rows(layout: dict[str, Any], org_id: str) -> Rows:
    """The site, zone, bench and device rows that layout.json describes."""
    site = {"org_id": org_id, "site_id": layout["site"]}
    rows: Rows = {
        Site: [site | {"name": layout["name"], "timezone": layout["timezone"]}],
        Zone: [],
        Bench: [],
        Device: [],
    }
    for zone in layout["zones"]:
        rows[Zone].append(
            site
            | {
                "zone_id": zone["id"],
                "name": zone["name"],
                "reservoir_id": zone["reservoir"]["id"],
                "reservoir_usable_l": zone["reservoir"]["usableL"],
                "dosing_cabinet_id": zone["dosingCabinet"],
                "dose_lines": zone["doseLines"],
                "day_tanks": zone["dayTanks"],
                "sensors": zone.get("sensors", {}),
            }
        )
        for bench in zone["benches"]:
            rows[Bench].append(
                site
                | {
                    "bench_id": bench["id"],
                    "zone_id": zone["id"],
                    "channels": bench["channels"],
                    "length_m": bench["lengthM"],
                }
            )
    for device in layout["devices"]:
        rows[Device].append(
            site | {"device_id": device["id"], "kind": device["kind"], "zone_id": device.get("zone")}
        )
    return rows


def seed(engine: Engine, client_dir: Path) -> Rows:
    """Make the database match client_dir/layout.json. Returns the rows it wrote."""
    path = client_dir / "layout.json"
    layout = json.loads(path.read_text(encoding="utf-8"))
    try:
        rows = layout_rows(layout, org_id=client_dir.name)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{path}: missing or malformed field {exc}") from exc
    with engine.begin() as connection:
        write_rows(connection, rows)
    return rows


def write_rows(connection: Connection, rows: Rows) -> None:
    """Make the site's rows match `rows` (layout_rows), in the caller's transaction."""
    org_id, site_id = rows[Site][0]["org_id"], rows[Site][0]["site_id"]
    for model in (Site, Zone, Bench, Device):  # parents first
        _upsert(connection, model, rows[model])
    for model in (Bench, Device, Zone):  # children first
        _remove_missing(connection, model, rows[model], org_id, site_id)


def _upsert(connection: Connection, model: type[Base], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    table = model.__table__
    keys = [column.name for column in table.primary_key.columns]
    statement = insert(table).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=keys,
        set_={name: statement.excluded[name] for name in rows[0] if name not in keys},
    )
    connection.execute(statement)


def _remove_missing(
    connection: Connection, model: type[Base], rows: list[dict[str, Any]], org_id: str, site_id: str
) -> None:
    """Delete this site's rows of `model` whose ID is no longer in the layout."""
    table = model.__table__
    (own_id,) = [c for c in table.primary_key.columns if c.name not in ("org_id", "site_id")]
    keep = [row[own_id.name] for row in rows]
    connection.execute(
        delete(table).where(table.c.org_id == org_id, table.c.site_id == site_id, own_id.not_in(keep))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load a client's layout.json into the database.")
    parser.add_argument(
        "client_dir", nargs="?", type=Path, help="config pack folder (default: CLIENT_DIR)"
    )
    args = parser.parse_args(argv)
    settings = load_settings()
    client_dir = args.client_dir or settings.client_dir
    engine = create_db_engine(settings)
    try:
        rows = seed(engine, client_dir)
    finally:
        engine.dispose()
    site = rows[Site][0]
    counts = ", ".join(f"{len(rows[model])} {model.__tablename__}" for model in (Zone, Bench, Device))
    print(f"seeded {site['org_id']}/{site['site_id']} from {client_dir / 'layout.json'}: {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
