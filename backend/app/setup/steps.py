"""The client setup's steps (T1-40): which config pack files each one fills, and its problems.

A setup is a config pack (contract §2.5) filled in one step at a time. Every step is one or more of
the pack's 12 files, so a saved setup is an ordinary pack: it is stored by the same import, after
the same checks, as a pack folder (app.db.config_store). The site step comes first, because the
other files are checked against its layout.json. The safety limits (limits.json) need the Owner.

A step's problems are the pack checks' problems about that step's files (app.packcheck), checked
with every file the draft has so far, so a limit is checked against the layout it limits. The
review is every step's problems together; the setup can be saved only when there are none.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .. import packcheck

LAYOUT = "layout.json"


@dataclass(frozen=True)
class Step:
    id: str
    title: str
    files: tuple[str, ...]
    owner_only: bool = False  # only an Owner may fill it in (contract §13: the safety limits)


STEPS = (
    Step("site", "Site, zones and devices", (LAYOUT,)),
    Step("features", "Features", ("features.json",)),
    Step("crops", "Crops and recipes", ("recipes.json",)),
    Step("nutrients", "Nutrients and stock tanks", ("nutrients.json",)),
    Step("limits", "Safety limits", ("limits.json",), owner_only=True),
    Step("alerts", "Alerts", ("alerts.json",)),
    Step("climate", "Climate", ("climate.json",)),
    Step("equipment", "Equipment and wiring", ("hardware.json", "io_map.json")),
    Step("simulator", "Simulator and scenarios", ("simulator.json", "scenarios.json")),
    Step("users", "Users", ("users.json",)),
)
STEP_BY_ID = {step.id: step for step in STEPS}
assert sorted(name for step in STEPS for name in step.files) == sorted(packcheck.PACK_FILES)

MAX_FILE_CHARS = 256_000  # a pack file is a few kB; this stops a mistaken paste, not a real file


def file_text(data: Any) -> str:
    """How a draft stores a file it was sent as JSON: two-space indents, as the pack folders are."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def problems_by_step(org_id: str, site_id: str, files: Mapping[str, str]) -> dict[str, list[str]]:
    """Every step's problems, "file: what is wrong" each; an empty list for a step that is ready."""
    found: dict[str, list[str]] = {step.id: [] for step in STEPS}
    step_of = {name: step.id for step in STEPS for name in step.files}
    for name in packcheck.PACK_FILES:
        if name not in files:
            found[step_of[name]].append(f"{name}: not filled in yet")
    prefix = f"{org_id}/"
    for problem in packcheck.validate_texts(org_id, files):
        if not problem.startswith(prefix):
            continue  # "<org>: missing <file>": said above, in the step's own words
        text = problem.removeprefix(prefix)
        name = text.partition(":")[0]
        if name in step_of:
            found[step_of[name]].append(text)
    layout = _layout(files)
    if layout is None:
        for step in STEPS:
            if step.id != "site" and any(name in files for name in step.files):
                found[step.id].append(f"{step.files[0]}: fill in the site step first; this step is checked against it")
    elif layout.get("site") != site_id:
        found["site"].append(f"{LAYOUT}: site must be {site_id!r}, the site this setup was started for; "
                             f"got {layout.get('site')!r}")
    return found


def devices(files: Mapping[str, str]) -> list[str]:
    """The device IDs in the draft's layout.json: the devices that get a login when it is saved."""
    layout = _layout(files) or {}
    listed = layout.get("devices") if isinstance(layout.get("devices"), list) else []
    return [device["id"] for device in listed if isinstance(device, dict) and isinstance(device.get("id"), str)]


def _layout(files: Mapping[str, str]) -> dict[str, Any] | None:
    try:
        layout = json.loads(files[LAYOUT])
    except (KeyError, ValueError):
        return None
    return layout if isinstance(layout, dict) else None


def as_new_client(name: str, text: str, org_id: str, site_id: str) -> str:
    """A file of this farm's pack made the new client's starting point: _meta.client is the new
    client, and layout.json's site is the new site. Everything else is kept, to be changed."""
    data = json.loads(text)
    if isinstance(data, dict):
        if isinstance(data.get("_meta"), dict) and "client" in data["_meta"]:
            data["_meta"]["client"] = org_id
        if name == LAYOUT:
            data["site"] = site_id
    return file_text(data)
