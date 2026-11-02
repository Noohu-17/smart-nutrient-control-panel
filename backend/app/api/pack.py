"""Reading the client's config pack (contract §2.5): the farm, and which site this API serves."""

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schemas import Farm

log = logging.getLogger("api")


class PackError(Exception):
    """The config pack can't be read: the server's problem, so a 500 from app.errors."""


@dataclass(frozen=True)
class SiteRef:
    """What every query is scoped by (OPEN-11)."""

    org_id: str
    site_id: str


def _without_meta(value: Any) -> Any:
    """The same data with every _meta block removed: they are never sent (§11.2)."""
    if isinstance(value, dict):
        return {key: _without_meta(entry) for key, entry in value.items() if key != "_meta"}
    if isinstance(value, list):
        return [_without_meta(entry) for entry in value]
    return value


def _cannot_load(reason: Exception) -> PackError:
    log.error("cannot load the config pack: %s", reason)
    return PackError("The farm layout could not be loaded")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_farm(client_dir: Path) -> Farm:
    """layout.json with one more field, features, the contents of features.json (§11.2)."""
    try:
        layout = _read_json(client_dir / "layout.json")
        features = _read_json(client_dir / "features.json")
        return Farm.model_validate(_without_meta({**layout, "features": features}))
    except (OSError, TypeError, ValueError) as exc:  # a ValidationError is a ValueError
        raise _cannot_load(exc) from exc


def site_of(client_dir: Path) -> SiteRef:
    """org_id is the client's folder name until organisations exist (OPEN-11), as in the seed."""
    try:
        return SiteRef(client_dir.name, _read_json(client_dir / "layout.json")["site"])
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise _cannot_load(exc) from exc
