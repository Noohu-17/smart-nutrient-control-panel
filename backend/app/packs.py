"""Where the farm's settings come from: a client's config pack (contract §2.5), read either from
its folder or from the database (T1-39), as the CONFIG_SOURCE setting says.

Every reader asks a Pack for a file by name, so nothing else needs to know where the pack lives:

- FolderPack: the folder CLIENT_DIR. Each file is read when it is asked for, as before T1-39, so
  a pack edited on a laptop is seen at once. The source in dev.
- StoredPack: one version of the pack in the database (app.db.config_store). The source on a
  server.

In database mode the newest stored version is used. The last one loaded is kept in memory: a new
version is read once, and if the database stops answering the API carries on with the version it
already has, as a controller keeps its last good config (§7.7). Stop doesn't ask the database at
all: it takes the version kept in memory (kept_pack) and has it refreshed in the background
(refresh_soon), so a hanging or busy database never holds Stop up (§8.3 rule 9). There is never a fallback from the database to the folder: a server reads only
what was imported and checked.
"""

import json
import logging
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import InterfaceError, OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as NoConnectionFree

from .config import PACK, Settings

log = logging.getLogger("api")

# The database can't answer right now: it's down, restarting, busy, or not migrated yet.
DATABASE_NOT_READY = (OperationalError, InterfaceError, ProgrammingError, NoConnectionFree)


class PackError(Exception):
    """The config pack can't be read: the server's problem, so a 500 from app.errors."""


class NotImported(PackError):
    """CONFIG_SOURCE is database and no pack has been imported for this organisation yet."""


class Pack:
    """A client's config pack: its JSON files by name, and which organisation it belongs to."""

    org_id: str
    version: int | None  # the stored version (configVersion, §7.7); None for a folder

    def text(self, name: str) -> str:
        """The file's text. FileNotFoundError when the pack has no such file."""
        raise NotImplementedError

    def json(self, name: str) -> Any:
        """The file, parsed. FileNotFoundError when the pack has no such file."""
        return json.loads(self.text(name))

    def optional(self, name: str) -> Any:
        """A file an older or smaller pack may not have: None if it isn't there."""
        try:
            return self.json(name)
        except FileNotFoundError:
            return None

    def describe(self) -> str:
        raise NotImplementedError


class FolderPack(Pack):
    """The pack in its folder, config/clients/<client>."""

    def __init__(self, folder: Path, org_id: str | None = None):
        self.folder = folder
        self.org_id = org_id or folder.name  # the folder's name until organisations exist (OPEN-11)
        self.version = None

    def text(self, name: str) -> str:
        return (self.folder / name).read_text(encoding="utf-8")

    def describe(self) -> str:
        return str(self.folder)


class StoredPack(Pack):
    """One version of an organisation's pack, as stored in the database. Each file is parsed once."""

    def __init__(
        self, org_id: str, version: int, files: Mapping[str, str], device_config_sent_at: datetime | None = None
    ):
        self.org_id, self.version, self.files = org_id, version, dict(files)
        self.device_config_sent_at = device_config_sent_at
        self._parsed: dict[str, Any] = {}

    def text(self, name: str) -> str:
        if name not in self.files:
            raise FileNotFoundError(f"{self.org_id} config version {self.version} has no {name}")
        return self.files[name]

    def json(self, name: str) -> Any:
        if name not in self._parsed:
            self._parsed[name] = super().json(name)
        # a copy, so a caller changing what it got can't change the next caller's answer
        return json.loads(json.dumps(self._parsed[name]))

    def describe(self) -> str:
        return f"{self.org_id} config version {self.version} in the database"


def as_pack(pack: "Pack | Path") -> Pack:
    """A Pack, also from a bare folder path (scripts and tests that point at a pack folder)."""
    return pack if isinstance(pack, Pack) else FolderPack(pack)


_kept: dict[tuple[str, str], StoredPack] = {}  # (database, org) → the newest version loaded
_kept_lock = threading.Lock()


def load_pack(settings: Settings, engine: Engine) -> Pack:
    """The pack CONFIG_SOURCE names: the folder CLIENT_DIR, or the organisation's newest version
    in the database. NotImported if nothing has been imported; PackError if the database can't
    answer and no version has been loaded yet."""
    if settings.config_source == PACK:
        return FolderPack(settings.client_dir, settings.org_id)
    from .db import config_store  # the models need nothing from here, but keep start-up light

    key = _key(settings, engine)
    kept = _kept.get(key)
    try:
        with engine.connect() as connection:
            version = config_store.latest_version(connection, settings.org_id)
            if version is None:
                raise NotImported(f"no config pack has been imported for {settings.org_id}")
            if kept is not None and kept.version == version:
                return kept
            pack = config_store.read_version(connection, settings.org_id, version)
    except DATABASE_NOT_READY as exc:
        if kept is not None:
            log.warning("database not answering: using %s, kept in memory", kept.describe())
            return kept
        log.error("cannot load the config pack from the database: %s", str(getattr(exc, "orig", exc)).partition("\n")[0])
        raise PackError("The farm settings could not be loaded") from exc
    with _kept_lock:
        newest = _kept.get(key)
        if newest is not None and newest.version is not None and newest.version > version:
            return newest  # another request already loaded a newer one
        _kept[key] = pack
    log.info("loaded %s", pack.describe())
    return pack


def _key(settings: Settings, engine: Engine) -> tuple[str, str]:
    return (engine.url.render_as_string(hide_password=True), settings.org_id)


def kept_pack(settings: Settings, engine: Engine) -> Pack | None:
    """The pack without asking the database: the folder in dev, else the newest version loaded so
    far, or None if none has been loaded yet."""
    if settings.config_source == PACK:
        return FolderPack(settings.client_dir, settings.org_id)
    return _kept.get(_key(settings, engine))


_refreshing: set[tuple[str, str]] = set()  # (database, org) being refreshed in the background
_refreshing_lock = threading.Lock()


def refresh_soon(settings: Settings, engine: Engine) -> None:
    """Look for a newer version in the background, one look at a time per organisation, so the
    caller never waits for the database."""
    if settings.config_source == PACK:
        return
    key = _key(settings, engine)
    with _refreshing_lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def refresh() -> None:
        try:
            load_pack(settings, engine)
        except PackError as exc:
            log.warning("config pack not refreshed: %s", exc)
        except Exception:  # a background thread has no caller to tell
            log.exception("config pack not refreshed")
        finally:
            with _refreshing_lock:
                _refreshing.discard(key)

    threading.Thread(target=refresh, daemon=True, name="pack-refresh").start()


def forget_kept() -> None:
    """Drop the versions kept in memory (tests)."""
    with _kept_lock:
        _kept.clear()
