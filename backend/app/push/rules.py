"""Who gets a pushed alert, from the config pack's alerts.json (contract §2.5, T1-41).

    "delivery": ["IN_APP", "WEB_PUSH"]          the site pushes alerts only with WEB_PUSH
    "events": { "LEAK_DETECTED": { "notifyRoles": ["OWNER", "AGRONOMIST", "OPERATOR"] } }

A kind that alerts.json doesn't list goes to DEFAULT_ROLES, so a new kind is never left unsent.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Connection, delete

from ..db.models import PushSubscription

if TYPE_CHECKING:
    from ..packs import Pack

WEB_PUSH = "WEB_PUSH"
DEFAULT_ROLES = ("OWNER", "AGRONOMIST", "OPERATOR")
SEVERITIES = ("CRITICAL", "WARNING")  # what a browser can ask for; INFO raises no alert (§6.5)


@dataclass(frozen=True)
class PushRules:
    enabled: bool
    roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # notifyRoles by alert kind

    def roles_for(self, kind: str) -> tuple[str, ...]:
        return self.roles.get(kind, DEFAULT_ROLES)

    @classmethod
    def from_pack(cls, source: "Pack | Path") -> "PushRules":
        """From a Pack (app.packs: the folder in dev, the imported version on a server) or a folder."""
        from ..packs import as_pack

        alerts = as_pack(source).optional("alerts.json")
        if not isinstance(alerts, dict):
            return cls(enabled=False)
        delivery = alerts.get("delivery") or []
        roles = {
            kind: tuple(spec["notifyRoles"])
            for kind, spec in (alerts.get("events") or {}).items()
            if isinstance(spec, dict) and isinstance(spec.get("notifyRoles"), list)
        }
        return cls(enabled=isinstance(delivery, list) and WEB_PUSH in delivery, roles=roles)


def forget_user(connection: Connection, org_id: str, site_id: str, user_id: str) -> None:
    """No more pushes to any browser of this user: for sign-out everywhere, a new role, turning off
    and an invite, as their sessions end too."""
    connection.execute(delete(PushSubscription).where(
        PushSubscription.org_id == org_id, PushSubscription.site_id == site_id, PushSubscription.user_id == user_id,
    ))
