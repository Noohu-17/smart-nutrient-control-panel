"""Pydantic models for the MQTT messages in docs/contract.md.

Use parse_farm_message() on a decoded farm → cloud message. The simulator's telemetry
is checked against the same models.
"""

from .envelope import ContractModel, Envelope
from .farm_messages import (
    FARM_MESSAGE_MODELS,
    ClimateState,
    ClimateTelemetry,
    DeviceStatus,
    DeviceStatusLastWill,
    PowerState,
    ZoneState,
    ZoneTelemetry,
    parse_farm_message,
)

__all__ = [
    "FARM_MESSAGE_MODELS",
    "ClimateState",
    "ClimateTelemetry",
    "ContractModel",
    "DeviceStatus",
    "DeviceStatusLastWill",
    "Envelope",
    "PowerState",
    "ZoneState",
    "ZoneTelemetry",
    "parse_farm_message",
]
