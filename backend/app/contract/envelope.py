"""Message envelope (contract §5) and the field types every contract message shares.

Python fields are snake_case; on the wire they are lowerCamelCase (contract §1), so
`msg_id` reads and writes `msgId`. Unknown fields are ignored, not rejected (contract
§1), so additive contract changes don't break the back end. Numbers and booleans are
strict: `"1.62"` or `1` where the contract wants `1.62` or `true` is an error.
"""

from datetime import timedelta
from typing import Annotated, Any, Literal

from pydantic import (
    UUID4,
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    StringConstraints,
)
from pydantic.alias_generators import to_camel


def _require_utc(value: AwareDatetime) -> AwareDatetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("time must be in UTC (contract §1)")
    return value


def exact_type(kind: type) -> BeforeValidator:
    """Strict mode for a Literal (pydantic has none), so `true` is not taken for 1 or `0` for false."""

    def check(value: Any) -> Any:
        if type(value) is not kind:
            raise ValueError(f"must be {kind.__name__}, not {type(value).__name__}")
        return value

    return BeforeValidator(check)


UtcTime = Annotated[AwareDatetime, AfterValidator(_require_utc)]
Version = Annotated[Literal[1], exact_type(int)]  # every message carries "v": 1 (contract §1)
Number = Annotated[float, Strict()]  # a JSON number, never a string (contract §1)
Count = Annotated[int, Strict(), Field(ge=0)]
Reading = Number | None  # null when the sensor is missing or failed, never a fake 0 (contract §1)

# A percentage with no quality next to it (setpoints, UPS battery): always 0–100. Telemetry
# readings carry a quality, and their limits depend on it (farm_messages.Limits, §6.1).
Percent = Annotated[Number, Field(ge=0, le=100)]

SiteId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]  # lowercase slug (§2.1)
ZoneId = Annotated[str, StringConstraints(pattern=r"^Z[0-9]+$")]  # Z{n} (§2.1)
DeviceId = Annotated[str, StringConstraints(min_length=1)]  # GW1, ZC1, … from the layout (§2.2)


class ContractModel(BaseModel):
    """Base for every contract model: camelCase on the wire, unknown fields ignored."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=True,
        serialize_by_alias=True,
        extra="ignore",
    )


class Envelope(ContractModel):
    """The fields at the top level of every MQTT message (contract §5)."""

    v: Version
    type: str
    msg_id: UUID4
    ts: UtcTime  # real time the reading was taken or the event happened
    site: SiteId
    device: DeviceId
    boot_id: Count
    seq: Count
    sim_ts: UtcTime | None = None  # playground sites only: simulated time (§5, §11.4)
