"""Database models (SQLAlchemy 2): site, zone, bench, device, reading, latest message and user.

Every table has org_id and site_id (OPEN-11), so more organisations and sites can share
the database later. Rows are keyed by the contract's own IDs (§2.1, §2.2): siet-ph1, Z1,
Z1-B1, ZC1. Until the organisation layer exists, org_id is the client's config-pack
folder name, for example client_A.

The farm itself (zones, benches, devices) comes from layout.json through app.db.seed;
nothing farm-specific is typed into code.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    BigInteger,
    ForeignKeyConstraint,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ID = String(64)
Timestamp = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    # Fixed constraint names, so migrations and the database always agree on them.
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


def _site_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(["org_id", "site_id"], ["site.org_id", "site.site_id"])


def _zone_fk() -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["org_id", "site_id", "zone_id"], ["zone.org_id", "zone.site_id", "zone.zone_id"]
    )


class Site(Base):
    """One farm site (layout.json "site", "name" and "timezone")."""

    __tablename__ = "site"
    # Site IDs are in every MQTT topic, farm/{site}/… (contract §4), so they are unique
    # across organisations too.
    __table_args__ = (UniqueConstraint("site_id"),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)  # siet-ph1
    name: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text)


class Zone(Base):
    """A growing zone with its reservoir, dosing lines and day-tanks (layout.json "zones")."""

    __tablename__ = "zone"
    __table_args__ = (_site_fk(),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    zone_id: Mapped[str] = mapped_column(ID, primary_key=True)  # Z1
    name: Mapped[str] = mapped_column(Text)  # what the app shows, e.g. Zone A
    reservoir_id: Mapped[str] = mapped_column(ID)
    reservoir_usable_l: Mapped[float]
    dosing_cabinet_id: Mapped[str] = mapped_column(ID)
    dose_lines: Mapped[list[str]] = mapped_column(ARRAY(Text))
    day_tanks: Mapped[dict[str, Any]] = mapped_column(JSONB)  # keyed by dose line, as in layout.json
    sensors: Mapped[dict[str, Any]] = mapped_column(JSONB)  # optional sensors fitted, e.g. dissolvedOxygen


class Bench(Base):
    """A bench (the handoff's "NFT table") and its channels (layout.json zones[].benches)."""

    __tablename__ = "bench"
    __table_args__ = (_zone_fk(),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    bench_id: Mapped[str] = mapped_column(ID, primary_key=True)  # Z1-B1
    zone_id: Mapped[str] = mapped_column(ID)
    channels: Mapped[int]
    length_m: Mapped[float]


class Device(Base):
    """Anything that connects to MQTT (layout.json "devices", contract §2.2)."""

    __tablename__ = "device"
    __table_args__ = (_site_fk(), _zone_fk())

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    device_id: Mapped[str] = mapped_column(ID, primary_key=True)  # ZC1
    kind: Mapped[str] = mapped_column(ID)  # GATEWAY, ZONE_CONTROLLER, CLIMATE_CONTROLLER, POWER_MONITOR
    zone_id: Mapped[str | None] = mapped_column(ID)  # a zone controller's zone
    # Set by the worker and kept by the seed. last_seen is the newest ts of any message from the
    # device (§5, §11.2): the message's own time, so a retained or replayed one can't make a
    # silent device look alive. last_will_at is when its last will (§6.4) arrived; the device is
    # offline from then until it sends something new, which clears it (§14).
    last_seen: Mapped[datetime | None] = mapped_column(Timestamp)
    last_will_at: Mapped[datetime | None] = mapped_column(Timestamp)


class Reading(Base):
    """One sensor value at one time. A TimescaleDB hypertable, partitioned on ts.

    One row per value, so any sensor the layout configures fits without a schema change
    (OPEN-12). metric is the contract's field name (ec, ph, solTempC, airRh …); values
    per dose line or sensor position add the key after a dot (dayTankPct.A,
    airTempByPosC.PAD_END). value is null when the sensor is missing or failed, and
    quality says why (contract §1, §6.1). A repeated message gives the same primary
    key, so replays can be dropped with ON CONFLICT DO NOTHING.

    No foreign key to device: readings keep their history when a device leaves the layout.
    """

    __tablename__ = "reading"

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    device_id: Mapped[str] = mapped_column(ID, primary_key=True)
    metric: Mapped[str] = mapped_column(String(128), primary_key=True)
    ts: Mapped[datetime] = mapped_column(Timestamp, primary_key=True, index=True)  # real time (§1)
    sim_ts: Mapped[datetime | None] = mapped_column(Timestamp)  # simulated time, playground sites only
    zone_id: Mapped[str | None] = mapped_column(ID)  # null for climate and power readings
    value: Mapped[float | None]
    quality: Mapped[str | None] = mapped_column(String(32))  # OK, STALE, OUT_OF_RANGE, FAULT, UNCALIBRATED


class LatestMessage(Base):
    """The newest message of each kind that carries a device's state: one row per device and type.

    ZONE_STATE (§6.8) and CLIMATE_STATE (§6.9), and ZONE_TELEMETRY (§6.1) because §11.2 takes
    leak and activeCmdId from it. A message replaces the row only when its (bootId, seq) is
    newer (§5), so a late, replayed or retained one never replaces a newer one.

    No foreign key to device, as for readings.
    """

    __tablename__ = "latest_message"

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    device_id: Mapped[str] = mapped_column(ID, primary_key=True)
    type: Mapped[str] = mapped_column(String(32), primary_key=True)  # ZONE_STATE …
    zone_id: Mapped[str | None] = mapped_column(ID)  # the message's zone; null for CLIMATE_STATE
    boot_id: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column(Timestamp)
    sim_ts: Mapped[datetime | None] = mapped_column(Timestamp)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the checked message, with its wire names


class User(Base):
    """A person who signs in (roles: contract §13).

    The table is app_user because user is a reserved word in PostgreSQL. Passwords
    never come from config (users.json "passwordFrom": "env"); the sign-in code sets
    password_hash.
    """

    __tablename__ = "app_user"
    __table_args__ = (_site_fk(), UniqueConstraint("org_id", "email"))

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))  # OWNER, AGRONOMIST, OPERATOR, VIEWER
    password_hash: Mapped[str | None] = mapped_column(Text)
