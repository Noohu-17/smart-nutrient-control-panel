"""Web Push subscriptions (contract §11.2, T1-41): the browser's side of notifications when the app
is closed.

    GET    /api/push/key             { publicKey }: the VAPID key for PushManager.subscribe
    POST   /api/push/subscriptions   { endpoint, keys: { p256dh, auth }, severities? }: this browser
                                     gets pushes; sent again, it changes the severities
    DELETE /api/push/subscriptions   { endpoint }: this browser gets no more pushes: 204

For every signed-in user except demo users: everyone who picks a role on the demo farm shares its
demo user, so their phones would get each other's pushes (403). 404 on a site whose alerts.json
doesn't list WEB_PUSH in delivery. The endpoint must be a push service's (endpoints.problem): 422
otherwise. A browser that subscribes again, as anyone, replaces its own row. Which alerts reach a
browser is the notifier's choice (push/notifier.py): the user's role in alerts.json notifyRoles,
and the severities asked for here: always CRITICAL, and WARNING when asked for too.
"""

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import FastAPI, Response
from pydantic import Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..api.routes import EngineDep, PackDep, SettingsDep, SiteDep, _database, _errors
from ..auth.access import SignedIn
from ..contract import ContractModel
from ..db.models import PushSubscription, User
from ..errors import ApiError
from . import endpoints
from .rules import PushRules

Severity = Literal["CRITICAL", "WARNING"]


def _base64url(value: str, size: int, what: str) -> str:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"{what} is not base64url") from exc
    if len(raw) != size:
        raise ValueError(f"{what} is not {size} bytes")
    return value


class PushKeys(ContractModel):
    p256dh: Annotated[str, Field(max_length=128)]  # the browser's P-256 public key, 65 bytes
    auth: Annotated[str, Field(max_length=64)]  # the browser's auth secret, 16 bytes

    @field_validator("p256dh")
    @classmethod
    def _p256dh(cls, value: str) -> str:
        return _base64url(value, 65, "p256dh")

    @field_validator("auth")
    @classmethod
    def _auth(cls, value: str) -> str:
        return _base64url(value, 16, "auth")


class NewSubscription(ContractModel):
    endpoint: Annotated[str, Field(max_length=endpoints.MAX_ENDPOINT)]
    keys: PushKeys
    severities: Annotated[list[Severity], Field(min_length=1, max_length=2, default_factory=lambda: ["CRITICAL"])]

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        if (why := endpoints.problem(value)) is not None:
            raise ValueError(f"not a push service's endpoint: {why}")
        return value


class Subscription(ContractModel):
    endpoint: str
    severities: list[Severity]


class Unsubscribe(ContractModel):
    endpoint: Annotated[str, Field(max_length=endpoints.MAX_ENDPOINT)]


class PushKey(ContractModel):
    public_key: str  # base64url, for PushManager.subscribe({ applicationServerKey })


def _push_on(pack) -> None:
    if not PushRules.from_pack(pack).enabled:
        raise ApiError(404, "NOT_FOUND", "This site doesn't send notifications")


def get_key(settings: SettingsDep, pack: PackDep, caller: SignedIn) -> PushKey:
    """The VAPID public key. 503 while the server has none set."""
    _push_on(pack)
    if not settings.vapid_public_key:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "Notifications aren't set up on the server yet")
    return PushKey(public_key=settings.vapid_public_key)


def subscribe(
    body: NewSubscription, pack: PackDep, engine: EngineDep, site: SiteDep, caller: SignedIn
) -> Subscription:
    """This browser gets pushes for the signed-in user. Sent again, it changes the severities."""
    _push_on(pack)
    now = datetime.now(UTC)
    severities = sorted(set(body.severities) | {"CRITICAL"})  # critical always notifies, as in the app (#91)
    with _database(engine) as connection:
        password_hash = connection.scalar(select(User.password_hash).where(
            User.org_id == site.org_id, User.site_id == site.site_id, User.user_id == caller.user_id,
        ))
        if password_hash is None:  # signed in without a password: a demo user (POST /api/auth/demo)
            raise ApiError(403, "FORBIDDEN", "Demo users can't turn on notifications: sign in with your own account")
        row = {
            "org_id": site.org_id, "site_id": site.site_id, "subscription_id": str(uuid.uuid4()),
            "user_id": caller.user_id, "endpoint": body.endpoint, "p256dh": body.keys.p256dh,
            "auth": body.keys.auth, "severities": severities, "created_at": now, "updated_at": now,
        }
        connection.execute(insert(PushSubscription).values(row).on_conflict_do_update(
            index_elements=["org_id", "site_id", "endpoint"],
            set_={key: row[key] for key in ("user_id", "p256dh", "auth", "severities", "updated_at")},
        ))
        connection.commit()
    return Subscription(endpoint=body.endpoint, severities=severities)


def unsubscribe(body: Unsubscribe, engine: EngineDep, site: SiteDep, caller: SignedIn) -> Response:
    """This browser gets no more pushes. Nothing to remove is fine too."""
    with _database(engine) as connection:
        connection.execute(delete(PushSubscription).where(
            PushSubscription.org_id == site.org_id, PushSubscription.site_id == site.site_id,
            PushSubscription.user_id == caller.user_id, PushSubscription.endpoint == body.endpoint,
        ))
        connection.commit()
    return Response(status_code=204)


def add_push_api(app: FastAPI) -> None:
    app.add_api_route("/api/push/key", get_key, response_model=PushKey, responses=_errors(401, 404, 503),
                      tags=["push"])
    app.add_api_route("/api/push/subscriptions", subscribe, methods=["POST"], response_model=Subscription,
                      responses=_errors(401, 403, 404, 422, 503), tags=["push"])
    app.add_api_route("/api/push/subscriptions", unsubscribe, methods=["DELETE"], status_code=204,
                      response_class=Response, responses=_errors(401, 403, 422, 503), tags=["push"])
