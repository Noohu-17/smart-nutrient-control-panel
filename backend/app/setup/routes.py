"""A new client's setup (contract §11.5, T1-40): a draft saved step by step, each step checked as it
is saved, a review, then the save that stores it as the new organisation's first config version
and issues its devices' logins, once.

    POST   /api/setup/drafts                       { orgId, siteId, startFrom? }: a new draft: 201
    GET    /api/setup/drafts                       the open drafts started on this site
    GET    /api/setup/drafts/{id}                  a draft, with every step's state and problems
    DELETE /api/setup/drafts/{id}                  discard an open draft: 204
    GET    /api/setup/drafts/{id}/steps/{step}     a step's files as they are so far
    PUT    /api/setup/drafts/{id}/steps/{step}     { files }: save a step's files; its problems answered
    GET    /api/setup/drafts/{id}/review           every step's problems, and the devices that get logins
    POST   /api/setup/drafts/{id}/save             store it as version 1 and issue the device logins

Owner and Agronomist set clients up; only an Owner fills in the safety limits (limits.json) and
saves (§13). Demo users (signed in with no password, POST /api/auth/demo) get 403 on every route:
anyone may pick a role on a demo farm, so they must not add organisations or get device logins. A step is saved even with problems, so a setup can be filled in over several sittings;
the save refuses a draft with any (422 SETUP_NOT_READY, the problems in details). A new client is a
new organisation with a new site: 409 if either already has settings or an open draft.

Saving runs the same import, with the same checks, as `python -m app.db.config_store import`, and
in the same transaction issues a login to every device in the layout (app.setup.logins): the answer
is the only place the passwords appear. This API keeps serving its own ORG_ID; a deployment serves
the new client once its ORG_ID is set to it (T1-39).
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Path, Response
from pydantic import Field
from sqlalchemy import Connection, delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..api.routes import EngineDep, PackDep, SiteDep, _database, _errors
from ..api.schemas import ApiTime
from ..auth import audit
from ..auth.access import AGRONOMIST, OWNER, Caller, allowed
from ..contract import ContractModel
from ..db import config_store
from ..db.models import ConfigVersion, DeviceLogin, SetupDraft, SetupDraftFile, Site, User
from ..errors import ApiError
from . import logins
from .steps import (
    MAX_FILE_CHARS,
    STEP_BY_ID,
    STEPS,
    as_new_client,
    devices,
    file_text,
    problems_by_step,
)

OPEN, SAVED, DISCARDED = "OPEN", "SAVED", "DISCARDED"



def _not_demo(roles_check):
    """roles_check, and 403 for a demo user: one signed in without a password (POST /api/auth/demo),
    as /push/subscriptions does. A user with no row here is refused too."""

    def check(caller: Annotated[Caller, Depends(roles_check)], engine: EngineDep, site: SiteDep) -> Caller:
        with _database(engine) as connection:
            password_hash = connection.scalar(select(User.password_hash).where(
                User.org_id == site.org_id, User.site_id == site.site_id, User.user_id == caller.user_id,
            ))
        if password_hash is None:
            raise ApiError(403, "FORBIDDEN", "Demo users can't set up a client: sign in with your own account")
        return caller

    return check


may_set_up = _not_demo(allowed(OWNER, AGRONOMIST, why="Only an Owner or an Agronomist sets up a client"))
MaySetUp = Annotated[Caller, Depends(may_set_up)]
may_save = _not_demo(allowed(OWNER, why="Only an Owner saves a client's settings"))
MaySave = Annotated[Caller, Depends(may_save)]

DraftId = Annotated[str, Path(alias="id", max_length=64)]
StepId = Annotated[str, Path(alias="step", max_length=32)]
OrgId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
SiteId = Annotated[str, Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=64)]  # §2.1: a lowercase slug


class NewDraft(ContractModel):
    org_id: OrgId
    site_id: SiteId
    # EMPTY: no files yet. THIS_FARM: start from a copy of this site's settings, made the new client's.
    start_from: Literal["EMPTY", "THIS_FARM"] = "EMPTY"


class StepFiles(ContractModel):
    files: dict[str, dict[str, Any]]  # file name → its JSON, e.g. {"layout.json": {...}}


class StepInfo(ContractModel):
    id: str
    title: str
    files: list[str]
    owner_only: bool  # only an Owner may save it (the safety limits)
    filled: bool  # every file of the step is in the draft
    problems: list[str]  # "file: what is wrong"; none and filled: the step is ready


class DraftInfo(ContractModel):
    id: str
    org_id: str
    site_id: str
    status: Literal["OPEN", "SAVED", "DISCARDED"]
    created_by: str
    created_at: ApiTime
    updated_at: ApiTime
    config_version: int | None  # once saved
    steps: list[StepInfo]


class DraftList(ContractModel):
    items: list[DraftInfo]


class StepContent(ContractModel):
    step: StepInfo
    files: dict[str, Any]  # the step's files so far, by name; a file not filled in yet is left out


class Review(ContractModel):
    draft: DraftInfo
    ready: bool  # no problems: an Owner may save it
    problems: list[str]
    devices: list[str]  # the devices that get a login when it is saved


class DeviceLoginOnce(ContractModel):
    device: str
    username: str  # the MQTT username: the device ID (§3)
    password: str  # answered this once; only its hash is kept


class Saved(ContractModel):
    org_id: str
    site_id: str
    config_version: int
    device_logins: list[DeviceLoginOnce]


# --- reading drafts --------------------------------------------------------------------------------


def _draft_row(connection: Connection, site, draft_id: str, *, lock: bool = False):
    """The draft, if it was started on this site. 404 otherwise, whoever else's it is."""
    query = select(SetupDraft.__table__).where(
        SetupDraft.draft_id == draft_id, SetupDraft.by_org_id == site.org_id, SetupDraft.by_site_id == site.site_id
    )
    row = connection.execute(query.with_for_update() if lock else query).first()
    if row is None:
        raise ApiError(404, "NOT_FOUND", f"There is no setup draft {draft_id}")
    return row


def _files(connection: Connection, draft_id: str) -> dict[str, str]:
    rows = connection.execute(
        select(SetupDraftFile.name, SetupDraftFile.content).where(SetupDraftFile.draft_id == draft_id)
    ).all()
    return {row.name: row.content for row in rows}


def _taken(connection: Connection, org_id: str, site_id: str) -> str | None:
    """Why this organisation or site can't be a new client, or None if it can."""
    if connection.scalar(select(ConfigVersion.version).where(ConfigVersion.org_id == org_id).limit(1)) is not None:
        return f"{org_id} already has settings; change them with a new version, not a new client"
    if connection.scalar(select(Site.org_id).where(Site.site_id == site_id)) is not None:
        return f"The site {site_id} is already in use"
    return None


def _steps(row, files: dict[str, str]) -> list[StepInfo]:
    problems = problems_by_step(row.org_id, row.site_id, files)
    return [
        StepInfo(
            id=step.id, title=step.title, files=list(step.files), owner_only=step.owner_only,
            filled=all(name in files for name in step.files), problems=problems[step.id],
        )
        for step in STEPS
    ]


def _info(row, files: dict[str, str]) -> DraftInfo:
    return DraftInfo(
        id=row.draft_id, org_id=row.org_id, site_id=row.site_id, status=row.status, created_by=row.created_by,
        created_at=row.created_at, updated_at=row.updated_at, config_version=row.saved_version,
        steps=_steps(row, files),
    )


def _open(row) -> None:
    if row.status != OPEN:
        raise ApiError(409, "CONFLICT", f"This setup draft is {row.status.lower()}; start a new one to change it")


def _step(step_id: str):
    step = STEP_BY_ID.get(step_id)
    if step is None:
        raise ApiError(404, "NOT_FOUND", f"There is no setup step {step_id}; the steps are {', '.join(STEP_BY_ID)}")
    return step


# --- the routes ------------------------------------------------------------------------------------


def start_draft(body: NewDraft, pack: PackDep, engine: EngineDep, site: SiteDep, caller: MaySetUp) -> DraftInfo:
    """A new client's draft. 409 if the organisation already has settings, the site is in use, or
    either has an open draft. start_from THIS_FARM copies this site's settings as a starting point."""
    now = datetime.now(UTC)
    draft_id = uuid.uuid4().hex
    copied: dict[str, str] = {}
    if body.start_from == "THIS_FARM":
        for step in STEPS:
            for name in step.files:
                try:
                    copied[name] = as_new_client(name, pack.text(name), body.org_id, body.site_id)
                except FileNotFoundError:
                    continue
    with _database(engine) as connection:
        why = _taken(connection, body.org_id, body.site_id)
        if why is not None:
            raise ApiError(409, "CONFLICT", why)
        try:
            connection.execute(insert(SetupDraft).values(
                draft_id=draft_id, org_id=body.org_id, site_id=body.site_id, by_org_id=site.org_id,
                by_site_id=site.site_id, created_by=caller.user_id, created_at=now, updated_at=now, status=OPEN,
            ))
        except IntegrityError as exc:
            connection.rollback()
            raise ApiError(409, "CONFLICT", f"{body.org_id} or {body.site_id} is already being set up") from exc
        if copied:
            connection.execute(insert(SetupDraftFile), [
                {"draft_id": draft_id, "name": name, "org_id": body.org_id, "site_id": body.site_id,
                 "content": text, "updated_by": caller.user_id, "updated_at": now}
                for name, text in copied.items()
            ])
        audit.write(connection, site, caller.user_id, caller.role, "SETUP_STARTED", draft_id,
                    {"orgId": body.org_id, "siteId": body.site_id, "startFrom": body.start_from})
        connection.commit()
        return _info(_draft_row(connection, site, draft_id), copied)


def list_drafts(engine: EngineDep, site: SiteDep, _caller: MaySetUp) -> DraftList:
    """The open drafts started on this site, newest first."""
    with _database(engine) as connection:
        rows = connection.execute(
            select(SetupDraft.__table__)
            .where(SetupDraft.by_org_id == site.org_id, SetupDraft.by_site_id == site.site_id,
                   SetupDraft.status == OPEN)
            .order_by(SetupDraft.created_at.desc())
        ).all()
        return DraftList(items=[_info(row, _files(connection, row.draft_id)) for row in rows])


def get_draft(draft_id: DraftId, engine: EngineDep, site: SiteDep, _caller: MaySetUp) -> DraftInfo:
    with _database(engine) as connection:
        row = _draft_row(connection, site, draft_id)
        return _info(row, _files(connection, draft_id))


def discard_draft(draft_id: DraftId, engine: EngineDep, site: SiteDep, caller: MaySetUp) -> Response:
    """Discard an open draft: it is kept, as DISCARDED, and its organisation and site are free again."""
    with _database(engine) as connection:
        row = _draft_row(connection, site, draft_id, lock=True)
        _open(row)
        connection.execute(update(SetupDraft).where(SetupDraft.draft_id == draft_id)
                           .values(status=DISCARDED, updated_at=datetime.now(UTC)))
        audit.write(connection, site, caller.user_id, caller.role, "SETUP_DISCARDED", draft_id, {"orgId": row.org_id})
        connection.commit()
    return Response(status_code=204)


def get_step(draft_id: DraftId, step_id: StepId, engine: EngineDep, site: SiteDep, _caller: MaySetUp) -> StepContent:
    step = _step(step_id)
    with _database(engine) as connection:
        row = _draft_row(connection, site, draft_id)
        files = _files(connection, draft_id)
    info = next(s for s in _steps(row, files) if s.id == step.id)
    return StepContent(step=info, files={name: json.loads(files[name]) for name in step.files if name in files})


def save_step(
    draft_id: DraftId, step_id: StepId, body: StepFiles, engine: EngineDep, site: SiteDep, caller: MaySetUp
) -> StepContent:
    """Save one step's files, all or some of them, and answer the step with its problems. The draft
    keeps them even with problems; 422 only for a file that isn't this step's, or is too big. 403 for
    the safety limits unless the caller is an Owner."""
    step = _step(step_id)
    if step.owner_only and caller.role != OWNER:
        raise ApiError(403, "FORBIDDEN", f"Only an Owner sets the {step.title.lower()}")
    wrong = sorted(set(body.files) - set(step.files))
    if wrong or not body.files:
        raise ApiError(422, "VALIDATION_ERROR",
                       f"The {step.id} step takes {', '.join(step.files)}" + (f", not {', '.join(wrong)}" if wrong else ""))
    texts = {name: file_text(data) for name, data in body.files.items()}
    too_big = [name for name, text in texts.items() if len(text) > MAX_FILE_CHARS]
    if too_big:
        raise ApiError(422, "VALIDATION_ERROR", f"{', '.join(too_big)}: more than {MAX_FILE_CHARS} characters")
    now = datetime.now(UTC)
    with _database(engine) as connection:
        row = _draft_row(connection, site, draft_id, lock=True)
        _open(row)
        connection.execute(delete(SetupDraftFile).where(SetupDraftFile.draft_id == draft_id,
                                                        SetupDraftFile.name.in_(list(texts))))
        connection.execute(insert(SetupDraftFile), [
            {"draft_id": draft_id, "name": name, "org_id": row.org_id, "site_id": row.site_id, "content": text,
             "updated_by": caller.user_id, "updated_at": now}
            for name, text in texts.items()
        ])
        connection.execute(update(SetupDraft).where(SetupDraft.draft_id == draft_id).values(updated_at=now))
        audit.write(connection, site, caller.user_id, caller.role, "SETUP_STEP_SAVED", draft_id,
                    {"step": step.id, "files": sorted(texts)})
        connection.commit()
        files = _files(connection, draft_id)
    info = next(s for s in _steps(row, files) if s.id == step.id)
    return StepContent(step=info, files=body.files)


def review(draft_id: DraftId, engine: EngineDep, site: SiteDep, _caller: MaySetUp) -> Review:
    """The whole setup before it is saved: every problem, and the devices that will get logins."""
    with _database(engine) as connection:
        row = _draft_row(connection, site, draft_id)
        files = _files(connection, draft_id)
        why = _taken(connection, row.org_id, row.site_id) if row.status == OPEN else None
    info = _info(row, files)
    problems = [problem for step in info.steps for problem in step.problems] + ([why] if why else [])
    return Review(draft=info, ready=row.status == OPEN and not problems, problems=problems, devices=devices(files))


def save_draft(
    draft_id: DraftId, response: Response, engine: EngineDep, site: SiteDep, caller: MaySave
) -> Saved:
    """Store the draft as the new organisation's version 1, load its farm, and issue a login to each
    of its devices, all in one transaction. 422 SETUP_NOT_READY with the problems if it has any; 409
    if it isn't open or the organisation or site was taken meanwhile. The answer carries the device
    passwords, so no browser or proxy may keep it (Cache-Control: no-store)."""
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    with _database(engine) as connection:
        row = _draft_row(connection, site, draft_id, lock=True)
        _open(row)
        files = _files(connection, draft_id)
        problems = [p for found in problems_by_step(row.org_id, row.site_id, files).values() for p in found]
        if problems:
            raise ApiError(422, "SETUP_NOT_READY", f"The setup has {len(problems)} problem(s) to fix first",
                           details={"problems": problems})
        why = _taken(connection, row.org_id, row.site_id)
        if why is not None:
            raise ApiError(409, "CONFLICT", why)
        try:
            imported, issued = _store(connection, row, files, caller, now)
        except config_store.Refused as exc:  # problems_by_step said none: only if the checks changed
            connection.rollback()
            raise ApiError(422, "SETUP_NOT_READY", "The setup did not pass the pack checks",
                           details={"problems": exc.problems}) from exc
        except IntegrityError as exc:  # the site was taken between the check and now
            connection.rollback()
            raise ApiError(409, "CONFLICT", f"The site {row.site_id} is already in use") from exc
        audit.write(connection, site, caller.user_id, caller.role, "SETUP_SAVED", draft_id, {
            "orgId": row.org_id, "siteId": imported.site_id, "configVersion": imported.version,
            "deviceLogins": [login.device for login in issued],
        })
        connection.commit()
    return Saved(org_id=row.org_id, site_id=imported.site_id, config_version=imported.version, device_logins=issued)


def _store(connection: Connection, row, files: dict[str, str], caller: Caller, now: datetime):
    """The draft's pack as the next version (config_store.import_on), its devices' logins, and the
    draft marked saved, in the caller's transaction. The logins' passwords are answered, not kept."""
    imported = config_store.import_on(connection, files, row.org_id, imported_from=f"setup {row.draft_id}", now=now)
    if imported.version != 1 or not imported.changed:  # an import for this organisation came in after the check
        raise ApiError(409, "CONFLICT", f"{row.org_id} already has settings; change them with a new version")
    issued = []
    for device in devices(files):
        password = logins.new_password()
        connection.execute(insert(DeviceLogin).values(
            org_id=row.org_id, site_id=imported.site_id, device_id=device,
            password_hash=logins.mosquitto_hash(password), issued_at=now, issued_by=caller.user_id,
            config_version=imported.version,
        ))
        issued.append(DeviceLoginOnce(device=device, username=device, password=password))
    connection.execute(update(SetupDraft).where(SetupDraft.draft_id == row.draft_id).values(
        status=SAVED, saved_version=imported.version, saved_at=now, updated_at=now,
    ))
    return imported, issued


def add_setup_api(app: FastAPI) -> None:
    tags = ["setup"]
    drafts, draft = "/api/setup/drafts", "/api/setup/drafts/{id}"
    app.add_api_route(drafts, start_draft, methods=["POST"], status_code=201, response_model=DraftInfo,
                      responses=_errors(401, 403, 409, 422, 503), tags=tags)
    app.add_api_route(drafts, list_drafts, response_model=DraftList, responses=_errors(401, 403, 503), tags=tags)
    app.add_api_route(draft, get_draft, response_model=DraftInfo, responses=_errors(401, 403, 404, 422, 503), tags=tags)
    app.add_api_route(draft, discard_draft, methods=["DELETE"], status_code=204,
                      responses=_errors(401, 403, 404, 409, 422, 503), tags=tags)
    app.add_api_route(f"{draft}/steps/{{step}}", get_step, response_model=StepContent,
                      responses=_errors(401, 403, 404, 422, 503), tags=tags)
    app.add_api_route(f"{draft}/steps/{{step}}", save_step, methods=["PUT"], response_model=StepContent,
                      responses=_errors(401, 403, 404, 409, 422, 503), tags=tags)
    app.add_api_route(f"{draft}/review", review, response_model=Review, responses=_errors(401, 403, 404, 422, 503),
                      tags=tags)
    app.add_api_route(f"{draft}/save", save_draft, methods=["POST"], status_code=201, response_model=Saved,
                      responses=_errors(401, 403, 404, 409, 422, 503), tags=tags)
