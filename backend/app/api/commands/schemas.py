"""Request and answer bodies for the command routes (contract §7.4, §7.8, §11.2, §11.3)."""

from typing import Literal

from pydantic import model_validator

from ...contract import ContractModel
from ...contract.farm_messages import CmdState, CommandLine, EcPh
from ..schemas import ApiTime
from .messages import StopScope


class StopBody(ContractModel):
    """{ "scope": "ALL_OPERATIONS" }, or { "scope": "ZONE", "zone": "Z1" } (§7.4)."""

    scope: StopScope
    zone: str | None = None

    @model_validator(mode="after")
    def _zone_only_with_scope_zone(self) -> "StopBody":
        if (self.zone is not None) != (self.scope == "ZONE"):
            raise ValueError("zone is needed with scope ZONE, and only then")
        return self


class ResumeBody(ContractModel):
    """{ "zone": "Z1" }, or { "scope": "CLIMATE" } (§7.8, §11.2)."""

    zone: str | None = None
    scope: Literal["CLIMATE"] | None = None

    @model_validator(mode="after")
    def _a_zone_or_climate(self) -> "ResumeBody":
        if (self.zone is None) == (self.scope is None):
            raise ValueError('give either a zone or "scope": "CLIMATE"')
        return self


class CommandSent(ContractModel):
    """202: the broker has the command (§11.3: { "cmdId": "6f1c…", "state": "DISPATCHED" })."""

    cmd_id: str
    state: Literal["DISPATCHED"]


class CommandStatus(ContractModel):
    """A command as its controller last reported it (CMD_STATE, §6.6)."""

    cmd_id: str
    device: str  # the controller that reported it
    state: CmdState
    reason: str | None  # a reason code (§8.4) when PARTIAL, FAILED, STOPPED or REJECTED
    lines: list[CommandLine] | None  # DOSE only: requested and delivered mL per line
    before: EcPh | None
    after: EcPh | None
    started_at: ApiTime | None
    ended_at: ApiTime | None
