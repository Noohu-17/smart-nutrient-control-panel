"""DEVICE_CONFIG (contract §7.7): each zone controller's limits, from the stored config (T1-39).

Controllers keep getting their limits this way, whatever the farm's settings are stored in. Each
version imported into the database (app.db.config_store) is one configVersion: its DEVICE_CONFIG
goes to every zone controller in its layout, retained, on farm/{site}/device/{controller}/config
(§4), so a controller that connects later still gets it. The controller keeps the last good one
and reports its configVersion back (§6.8).

The API sends it as CLOUD-api, the one back-end login allowed to (§4), the first time it reads a
version whose DEVICE_CONFIG hasn't been sent, and keeps trying in the background until the broker
has taken it; then config_version.device_config_sent_at is set. `python -m app.db.config_store
send-device-config` sends it again by hand. Before each try it checks that no newer version has
been imported, and stops if one has, so an old version never replaces a newer retained one. A pack folder (CONFIG_SOURCE=pack) has no version, so
nothing is sent from one.
"""

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from ..config import Settings
from ..packs import Pack, StoredPack
from .commands.messages import Command
from .commands.publish import NotSent, Publisher, publisher_for
from .pack import read_farm
from .schemas import contract_time

log = logging.getLogger("api")

ALARM_KEYS = ("solTempMaxC", "circFlowMinLpm")  # alerts.json alarms a controller acts on (§7.7)
RETRY_S = 30.0


def device_configs(pack: Pack, now: datetime) -> list[Command]:
    """One DEVICE_CONFIG per zone in the layout, for its controller.

    limits is limits.json zones.{zone}.limits as written there; alarms the alerts.json alarms in
    ALARM_KEYS that the pack gives. autoRefill is on only where features.json turns it on and
    limits.json gives the zone's autoRefill settings; otherwise {"enabled": false}, with no
    made-up numbers (§1).
    """
    if pack.version is None:
        raise ValueError("only a stored config version has a configVersion")
    farm = read_farm(pack)
    limits = pack.json("limits.json")["zones"]
    alerts = (pack.optional("alerts.json") or {}).get("zones") or {}
    auto_refill_on = farm.features.get("autoRefill") is True
    configs = []
    for zone in farm.zones:
        zone_limits = limits[zone.id]
        refill = zone_limits.get("autoRefill")
        alarms = (alerts.get(zone.id) or {}).get("alarms") or {}
        body: dict[str, Any] = {
            "v": 1,
            "type": "DEVICE_CONFIG",
            "msgId": str(uuid.uuid4()),
            "ts": contract_time(now),
            "site": farm.site,
            "device": "CLOUD",
            "configVersion": pack.version,
            "zone": zone.id,
            "limits": zone_limits["limits"],
            "autoRefill": {**refill, "enabled": True} if auto_refill_on and refill else {"enabled": False},
            "alarms": {key: alarms[key] for key in ALARM_KEYS if key in alarms},
        }
        topic = f"farm/{farm.site}/device/{zone.controller}/config"
        configs.append(Command(body["msgId"], topic, body))
    return configs


class Superseded(Exception):
    """A newer version has been imported: this one's DEVICE_CONFIG must not be sent."""


def _check_newest(engine: Engine, pack: StoredPack) -> None:
    """Superseded if a newer version is stored or being sent, so an old version retried after the
    broker comes back can't overwrite a newer retained DEVICE_CONFIG."""
    from ..db import config_store

    if _newest.get((_db(engine), pack.org_id), pack.version) > pack.version:
        raise Superseded(f"version {_newest[(_db(engine), pack.org_id)]} is newer")
    with engine.connect() as connection:
        newest = config_store.latest_version(connection, pack.org_id)
    if newest is not None and newest > pack.version:
        raise Superseded(f"version {newest} is stored")


def send(engine: Engine, pack: StoredPack, publisher: Publisher, now: datetime | None = None) -> int:
    """Send the version's DEVICE_CONFIG to every zone controller, retained, and record it.
    NotSent if the broker didn't take one; the others may have gone. Superseded, and nothing is
    sent, if a newer version exists. Returns how many were sent."""
    from ..db import config_store

    _check_newest(engine, pack)
    configs = device_configs(pack, now or datetime.now(UTC))
    for config in configs:
        publisher.publish(config.topic, config.body, retain=True)
        log.info("sent DEVICE_CONFIG version %d on %s", pack.version, config.topic)
    config_store.mark_device_config_sent(engine, pack.org_id, pack.version, datetime.now(UTC))
    pack.device_config_sent_at = datetime.now(UTC)
    return len(configs)


_started: set[tuple[str, str, int]] = set()  # (database, org, version) being sent or sent
_newest: dict[tuple[str, str], int] = {}  # (database, org) → the newest version being sent or sent
_started_lock = threading.Lock()


def _db(engine: Engine) -> str:
    return engine.url.render_as_string(hide_password=True)


def send_soon(settings: Settings, engine: Engine, pack: Pack, publisher: Publisher | None = None) -> None:
    """Start sending a stored version's DEVICE_CONFIG in the background if it hasn't been sent:
    the request that found the new version doesn't wait for the broker."""
    if not isinstance(pack, StoredPack) or pack.device_config_sent_at is not None:
        return
    key = (_db(engine), pack.org_id, pack.version)
    with _started_lock:
        if key in _started:
            return
        _started.add(key)
        org = (_db(engine), pack.org_id)
        _newest[org] = max(_newest.get(org, pack.version), pack.version)
    sender = publisher or publisher_for(settings)
    threading.Thread(target=_keep_trying, args=(engine, pack, sender), daemon=True, name="device-config").start()


def _keep_trying(engine: Engine, pack: StoredPack, publisher: Publisher, retry_s: float = RETRY_S) -> None:
    while True:
        try:
            send(engine, pack, publisher)
            return
        except Superseded as exc:
            log.info("DEVICE_CONFIG version %d not sent: %s", pack.version, exc)
            return
        except NotSent as exc:
            log.warning("DEVICE_CONFIG version %d not sent (%s); trying again in %.0f s", pack.version, exc, retry_s)
        except Exception:  # a thread that dies quietly would never send it: log it and try again
            log.exception("DEVICE_CONFIG version %d not sent; trying again in %.0f s", pack.version, retry_s)
        threading.Event().wait(retry_s)


def forget_started() -> None:
    """Forget which versions were sent (tests)."""
    with _started_lock:
        _started.clear()
        _newest.clear()
