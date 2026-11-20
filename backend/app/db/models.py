"""Database models (SQLAlchemy 2): site, zone, bench, device, reading, latest message, command
report, dose request, sent command, playground reset, alert, user, user session, audit log, and
the client's config pack, versioned (config version and config file, T1-39), and Web Push
subscriptions and the push outbox (T1-41).

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

    ZONE_STATE (§6.8) and CLIMATE_STATE (§6.9), ZONE_TELEMETRY (§6.1) because §11.2 takes
    leak and activeCmdId from it, and POWER_STATE (§6.3), which GET /power answers with. A message replaces the row only when its (bootId, seq) is
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


class CommandReport(Base):
    """The newest CMD_STATE (§6.6) of each command: one row per cmdId.

    Replaced only by a newer (bootId, seq) from the same device (§5), as LatestMessage is. The
    dose review adds up the delivered mL in it for the 24-hour totals (§11.4). No foreign key to
    device, as for readings.
    """

    __tablename__ = "command_report"

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    cmd_id: Mapped[str] = mapped_column(ID, primary_key=True)
    device_id: Mapped[str] = mapped_column(ID, index=True)
    state: Mapped[str] = mapped_column(String(32))  # ACCEPTED, EXECUTING, COMPLETED …
    boot_id: Mapped[int] = mapped_column(BigInteger)
    seq: Mapped[int] = mapped_column(BigInteger)
    ts: Mapped[datetime] = mapped_column(Timestamp)
    sim_ts: Mapped[datetime | None] = mapped_column(Timestamp)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the checked message, with its wire names


class DoseRequest(Base):
    """A reviewed dose request (§11.3), kept so it can be confirmed. Reviewing it changes nothing on the farm.

    limits is the snapshot the review used, which a confirmed command carries (§7.2).
    """

    __tablename__ = "dose_request"
    __table_args__ = (_zone_fk(),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    request_id: Mapped[str] = mapped_column(ID, primary_key=True)  # dr_…
    zone_id: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    projected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    estimate: Mapped[dict[str, Any]] = mapped_column(JSONB)
    can_confirm: Mapped[bool]
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requested_by: Mapped[str | None] = mapped_column(ID)  # the user_id; null before sign-in existed


class SentCommand(Base):
    """A command this API has sent to the farm (§7), exactly as sent.

    GET /api/commands/{cmdId} answers DISPATCHED from it until the first CMD_STATE arrives. A
    confirmed dose is saved just before it is sent, with its dose request and Idempotency-Key, so
    one dose request gives one command (§11.1, §11.3); dispatched_at is set once the broker has it.
    A stop or a resume is saved once it has been sent.
    """

    __tablename__ = "sent_command"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "site_id", "dose_request_id"],
            ["dose_request.org_id", "dose_request.site_id", "dose_request.request_id"],
        ),
        UniqueConstraint("org_id", "site_id", "dose_request_id"),
        UniqueConstraint("org_id", "site_id", "idempotency_key"),
    )

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    cmd_id: Mapped[str] = mapped_column(ID, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # DOSE, STOP, RESUME
    topic: Mapped[str] = mapped_column(Text)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB)  # the command, with its wire names
    issued_at: Mapped[datetime] = mapped_column(Timestamp)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    dispatched_at: Mapped[datetime | None] = mapped_column(Timestamp)  # null until the broker has it
    issued_by: Mapped[str | None] = mapped_column(ID)  # the user_id; null before sign-in existed
    dose_request_id: Mapped[str | None] = mapped_column(ID)  # a confirmed dose only
    idempotency_key: Mapped[str | None] = mapped_column(String(36))  # a UUID (§11.1)
    # The stuck-command guard (app/worker/stuck.py), both in server time: when the worker last
    # stored a newer CMD_STATE for it, and when it marked it UNKNOWN, TIMED_OUT for want of one.
    last_report_at: Mapped[datetime | None] = mapped_column(Timestamp)
    timed_out_at: Mapped[datetime | None] = mapped_column(Timestamp)


class PlaygroundReset(Base):
    """When the playground's farm was last reset (POST /api/sim/reset, contract §11.4): one row per site.

    A reset clears the 24-hour totals (§11.4). The simulator's controllers forget theirs, so the
    dose review counts only commands reported after reset_at (real time, as CMD_STATE ts is).
    """

    __tablename__ = "playground_reset"
    __table_args__ = (_site_fk(),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    reset_at: Mapped[datetime] = mapped_column(Timestamp)


class Alert(Base):
    """An alert the back end raised (app/alerts.py, contract §11.2): from a rule on the readings or
    from an EVENT (§6.5). `key` names the condition, so it is raised once while it lasts.

    All times are server time. first_seen_at is when the condition began; raised_at is null while
    it waits out its delay (alerts.json delayS), and such an alert is never listed. cleared_at is
    when the condition ended; acked_at and acked_by record who saw it.
    """

    __tablename__ = "alert"
    __table_args__ = (_site_fk(),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    alert_id: Mapped[str] = mapped_column(ID, primary_key=True)  # a UUID
    key: Mapped[str] = mapped_column(String(160), index=True)  # e.g. DAYTANK_LOW:Z1:A
    kind: Mapped[str] = mapped_column(String(32))  # EC_HIGH, ESTOP_PRESSED …
    severity: Mapped[str] = mapped_column(String(16))  # CRITICAL, WARNING, INFO
    zone_id: Mapped[str | None] = mapped_column(ID)
    message: Mapped[str] = mapped_column(Text)
    system_action: Mapped[str] = mapped_column(Text)
    next_step: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(Timestamp)
    raised_at: Mapped[datetime | None] = mapped_column(Timestamp)
    sim_ts: Mapped[datetime | None] = mapped_column(Timestamp)  # the farm clock, playground only
    cleared_at: Mapped[datetime | None] = mapped_column(Timestamp)
    acked_at: Mapped[datetime | None] = mapped_column(Timestamp)
    acked_by: Mapped[str | None] = mapped_column(ID)  # the user_id (before T1-35: the demo role)


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
    # Two-factor (T1-35): the authenticator secret, when it was turned on, the last code step used.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(Timestamp)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    disabled_at: Mapped[datetime | None] = mapped_column(Timestamp)  # turned off: can't sign in


class UserSession(Base):
    """A signed-in browser (contract §11.1, T1-35). The cookie holds a random ID and a signature;
    only the ID's SHA-256 is stored, so a copy of this table can't sign anyone in. ended_at is set
    by sign-out; a session also ends at expires_at. stage is FULL, or TOTP (the password was right,
    the authenticator code is next) or TOTP_SETUP (an Owner who must set up two-factor first)."""

    __tablename__ = "user_session"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "site_id", "user_id"],
            ["app_user.org_id", "app_user.site_id", "app_user.user_id"],
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 hex of the ID
    user_id: Mapped[str] = mapped_column(ID, index=True)
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    expires_at: Mapped[datetime] = mapped_column(Timestamp)
    ended_at: Mapped[datetime | None] = mapped_column(Timestamp)
    stage: Mapped[str] = mapped_column(String(16))
    failed_codes: Mapped[int]  # wrong authenticator codes in this session; 5 end it


class AuditLog(Base):
    """Who did what (contract §11.4, §13, T1-35): every sign-in and sign-out, and every action that
    changes the farm or the simulator. user_id is null for a failed sign-in; its detail says which
    email was tried. Rows are only ever added."""

    __tablename__ = "audit_log"
    __table_args__ = (_site_fk(),)

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    audit_id: Mapped[str] = mapped_column(ID, primary_key=True)  # a UUID
    at: Mapped[datetime] = mapped_column(Timestamp, index=True)
    user_id: Mapped[str | None] = mapped_column(ID)
    role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(32))  # SIGN_IN, DOSE_CONFIRM, STOP, SIM_RESET …
    target: Mapped[str | None] = mapped_column(Text)  # a cmdId, a dose request, an alert …
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ConfigVersion(Base):
    """One version of an organisation's settings: its config pack as imported (contract §2.5, T1-39).

    version counts up from 1 per organisation, and only when an import changes a file; it is the
    configVersion controllers get in DEVICE_CONFIG (§7.7) and report back (§6.8). site_id is the
    pack's own layout.json site. digest is the SHA-256 of every file's name and text, so an
    identical pack is seen without comparing files. device_config_sent_at: when the broker took
    this version's DEVICE_CONFIG for every zone controller; null until then. Rows are only added.
    """

    __tablename__ = "config_version"

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(ID)
    imported_at: Mapped[datetime] = mapped_column(Timestamp)
    imported_from: Mapped[str | None] = mapped_column(Text)  # e.g. the pack folder it came from
    digest: Mapped[str] = mapped_column(String(64))
    device_config_sent_at: Mapped[datetime | None] = mapped_column(Timestamp)


class ConfigFile(Base):
    """One file of a config version (layout.json, limits.json …), its text exactly as imported,
    with Windows line endings made plain, so an export gives the same pack back (T1-39)."""

    __tablename__ = "config_file"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "version"], ["config_version.org_id", "config_version.version"], ondelete="CASCADE"
        ),
    )

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(ID, primary_key=True)  # layout.json
    site_id: Mapped[str] = mapped_column(ID)
    content: Mapped[str] = mapped_column(Text)


class PushSubscription(Base):
    """A browser or phone that turned notifications on (Web Push, contract §11.2, T1-41): where the
    push service takes a push for it (endpoint) and the keys that encrypt it, from the browser,
    and which severities this user wants there (CRITICAL by default). One row per endpoint: a
    browser that subscribes again, even as another user, replaces its row."""

    __tablename__ = "push_subscription"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "site_id", "user_id"],
            ["app_user.org_id", "app_user.site_id", "app_user.user_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("org_id", "site_id", "endpoint"),
    )

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    subscription_id: Mapped[str] = mapped_column(ID, primary_key=True)  # a UUID
    user_id: Mapped[str] = mapped_column(ID, index=True)
    endpoint: Mapped[str] = mapped_column(Text)  # the push service's URL for this browser
    p256dh: Mapped[str] = mapped_column(String(128))  # the browser's public key, base64url
    auth: Mapped[str] = mapped_column(String(64))  # the browser's auth secret, base64url
    severities: Mapped[list[str]] = mapped_column(ARRAY(String(16)))  # CRITICAL, WARNING
    created_at: Mapped[datetime] = mapped_column(Timestamp)
    updated_at: Mapped[datetime] = mapped_column(Timestamp)


class PushOutbox(Base):
    """An alert to push (T1-41): the worker adds a row in the transaction that raises the alert
    (app/alerts.py), and the notifier (app/push/notifier.py) sends it and sets done_at. The key is
    the alert and its severity, so an alert is pushed once, and once more if it turns CRITICAL.
    outcome says what happened: SENT, or why nothing was sent (ACKNOWLEDGED, CLEARED, PUSH_OFF …);
    SENDING while the pushes are going out, after the row is taken (done_at)."""

    __tablename__ = "push_outbox"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "site_id", "alert_id"],
            ["alert.org_id", "alert.site_id", "alert.alert_id"],
            ondelete="CASCADE",
        ),
    )

    org_id: Mapped[str] = mapped_column(ID, primary_key=True)
    site_id: Mapped[str] = mapped_column(ID, primary_key=True)
    alert_id: Mapped[str] = mapped_column(ID, primary_key=True)
    severity: Mapped[str] = mapped_column(String(16), primary_key=True)
    queued_at: Mapped[datetime] = mapped_column(Timestamp, index=True)
    done_at: Mapped[datetime | None] = mapped_column(Timestamp)
    outcome: Mapped[str | None] = mapped_column(String(32))
    sent: Mapped[int | None]  # how many browsers took it
