"""Request bodies of the /api/sim routes (contract §11.4).

Only the shape is checked here: which values are allowed (speeds, drills, targets, ranges) comes
from the config pack, and the simulator checks them against the same files (§11.4.1), so the API
keeps no list of its own. A body is forwarded as these models write it, so a field they don't
name never reaches the simulator.
"""

import math
from typing import Annotated, Any

from pydantic import PlainValidator, StrictBool, StringConstraints, WithJsonSchema

from ...contract import ContractModel

# A zone, sensor, line or device ID, a drill kind or a metric: "Z1", "POLYHOUSE", "dayTankPct.ACID".
# Dot-separated words only, so a value can't change the path it is forwarded on.
SimId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*$", max_length=64)]


def _json_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number")  # noqa: TRY004 - pydantic makes only ValueError a 422
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("must be a finite number")
    return value


# A JSON number, never a string or true. It is forwarded as it came: 60 as 60, not 60.0.
JsonNumber = Annotated[int | float, PlainValidator(_json_number), WithJsonSchema({"type": "number"})]


class SpeedBody(ContractModel):
    """{ "x": 60 }: 0 (pause), 1, 60 or 288 (§11.4; the simulator's speedOptions)."""

    x: JsonNumber


class FaultBody(ContractModel):
    """{ "kind": "LEAK", "target": "Z1", "on": true }: Trigger (on) or Fix (off) a drill (§11.4.3)."""

    kind: SimId
    target: SimId
    on: StrictBool
    value: SimId | None = None  # for the drills that take one, such as ZONE_MODE LOCAL or MAINT


class HoldBody(ContractModel):
    """{ "target": "Z1", "metric": "ph", "value": 7.2, "gradual": true } (§11.4.2)."""

    target: SimId
    metric: SimId
    value: JsonNumber
    gradual: StrictBool = True  # §11.4.2: gradual by default
