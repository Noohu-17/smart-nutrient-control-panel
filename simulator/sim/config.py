"""Read the client's config pack (contract 2.5): the same files the back end loads.

T4-4 adds the device list from layout.json here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SITE_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")  # contract 2.1: lowercase slug


def read_pack_file(client_dir: Path, name: str) -> dict[str, Any]:
    path = client_dir / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"config pack file not found: {path}") from None
    except json.JSONDecodeError as err:
        raise ValueError(f"{path}: not valid JSON: {err}") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a JSON object")
    return data


def load_site(client_dir: Path) -> str:
    """The site ID used in every topic, farm/{site}/... (layout.json "site")."""
    site = read_pack_file(client_dir, "layout.json").get("site")
    if not isinstance(site, str) or not SITE_ID.fullmatch(site):
        raise ValueError(f"layout.json: site must be a lowercase slug, got {site!r}")
    return site
