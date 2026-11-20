"""Client settings in the database (contract §2.5, T1-39): each organisation's config pack,
versioned, imported from a pack folder and exported back to one.

Run from backend/, after `alembic upgrade head`:
    python -m app.db.config_store import ../config/clients/client_B     # check, store, load the farm
    python -m app.db.config_store export client_B ../exported/client_B  # newest version (or --version N)
    python -m app.db.config_store versions client_B
    python -m app.db.config_store send-device-config client_B           # DEVICE_CONFIG again (§7.7)

Import runs the very checks scripts/validate_config.py runs (app.packcheck) and refuses a pack
with any problem, storing nothing. A pack that differs from the newest stored version in any file
becomes the next version (configVersion, 1 for the first import); the same pack again changes
nothing. Either way the farm's site, zones, benches and devices are made to match its layout.json
(app.db.seed), in the same transaction. Stored text is the file's text with Windows line endings
made plain, so an export writes the pack back as it was. Versions are never changed or deleted.

The organisation is ORG_ID, or the pack folder's name until organisations exist (OPEN-11), as for
the seed: client_A's rows stay client_A's.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, Engine, Row, func, insert, select, update

from .. import packcheck
from ..config import load_settings
from ..packs import StoredPack
from .engine import create_db_engine
from .models import ConfigFile, ConfigVersion
from .seed import layout_rows, write_rows


class Refused(Exception):
    """The pack failed its checks; nothing was stored. problems: one line each."""

    def __init__(self, problems: list[str]):
        super().__init__(f"{len(problems)} problem(s): " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Imported:
    org_id: str
    site_id: str
    version: int
    changed: bool  # False: the pack was the newest version already


def read_folder(folder: Path) -> dict[str, str]:
    """Every JSON file in a pack folder, by name, its text with Windows line endings made plain."""
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} is not a folder")
    return {path.name: _plain(path.read_bytes().decode("utf-8")) for path in sorted(folder.glob("*.json"))}


def _plain(text: str) -> str:
    return text.replace("\r\n", "\n")


def digest(files: Mapping[str, str]) -> str:
    """SHA-256 of every file's name and text: equal for equal packs, whatever order they come in."""
    sha = hashlib.sha256()
    for name in sorted(files):
        for part in (name, files[name]):
            data = part.encode("utf-8")
            sha.update(len(data).to_bytes(8, "big") + data)
    return sha.hexdigest()


def import_pack(
    engine: Engine, files: Mapping[str, str], org_id: str, *, imported_from: str | None = None,
    now: datetime | None = None,
) -> Imported:
    """Check the pack, store it as the next version if it changed anything, and load its layout."""
    files = {name: _plain(text) for name, text in files.items()}
    problems = packcheck.validate_texts(org_id, files)
    if problems:
        raise Refused(problems)
    layout = json.loads(files["layout.json"])
    site_id = layout["site"]
    pack_digest = digest(files)
    with engine.begin() as connection:
        # One import at a time per organisation, so two can't both take the same next version.
        connection.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"config:{org_id}"))))
        newest = connection.execute(
            select(ConfigVersion.version, ConfigVersion.digest)
            .where(ConfigVersion.org_id == org_id)
            .order_by(ConfigVersion.version.desc())
            .limit(1)
        ).first()
        changed = newest is None or newest.digest != pack_digest
        version = 1 if newest is None else newest.version + int(changed)
        if changed:
            connection.execute(
                insert(ConfigVersion).values(
                    org_id=org_id, version=version, site_id=site_id, imported_at=now or datetime.now(UTC),
                    imported_from=imported_from, digest=pack_digest,
                )
            )
            connection.execute(
                insert(ConfigFile),
                [
                    {"org_id": org_id, "version": version, "name": name, "site_id": site_id, "content": text}
                    for name, text in sorted(files.items())
                ],
            )
        write_rows(connection, layout_rows(layout, org_id=org_id))
    return Imported(org_id, site_id, version, changed)


def import_folder(engine: Engine, folder: Path, org_id: str | None = None) -> Imported:
    return import_pack(engine, read_folder(folder), org_id or folder.name, imported_from=str(folder))


def latest_version(connection: Connection, org_id: str) -> int | None:
    return connection.scalar(select(func.max(ConfigVersion.version)).where(ConfigVersion.org_id == org_id))


def read_version(connection: Connection, org_id: str, version: int) -> StoredPack:
    """One stored version. LookupError if the organisation has no such version."""
    row = connection.execute(
        select(ConfigVersion.device_config_sent_at).where(
            ConfigVersion.org_id == org_id, ConfigVersion.version == version
        )
    ).first()
    if row is None:
        raise LookupError(f"{org_id} has no config version {version}")
    files = connection.execute(
        select(ConfigFile.name, ConfigFile.content).where(ConfigFile.org_id == org_id, ConfigFile.version == version)
    ).all()
    return StoredPack(org_id, version, {name: content for name, content in files}, row.device_config_sent_at)


def stored(engine: Engine, org_id: str, version: int | None = None) -> StoredPack:
    """The organisation's newest version, or `version`. LookupError if there is none."""
    with engine.connect() as connection:
        version = version if version is not None else latest_version(connection, org_id)
        if version is None:
            raise LookupError(f"no config pack has been imported for {org_id}")
        return read_version(connection, org_id, version)


def export_pack(engine: Engine, org_id: str, folder: Path, version: int | None = None) -> StoredPack:
    """Write a stored version's files into `folder`, made if needed, each as it was imported."""
    pack = stored(engine, org_id, version)
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in sorted(pack.files.items()):
        (folder / name).write_bytes(text.encode("utf-8"))
    return pack


def versions(engine: Engine, org_id: str) -> list[Row]:
    """Every stored version, oldest first: version, imported_at, imported_from, device_config_sent_at."""
    with engine.connect() as connection:
        return list(
            connection.execute(
                select(
                    ConfigVersion.version,
                    ConfigVersion.imported_at,
                    ConfigVersion.imported_from,
                    ConfigVersion.device_config_sent_at,
                )
                .where(ConfigVersion.org_id == org_id)
                .order_by(ConfigVersion.version)
            ).all()
        )


def mark_device_config_sent(engine: Engine, org_id: str, version: int, at: datetime) -> None:
    with engine.begin() as connection:
        connection.execute(
            update(ConfigVersion)
            .where(ConfigVersion.org_id == org_id, ConfigVersion.version == version)
            .values(device_config_sent_at=at)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Client settings in the database (T1-39).")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("import", help="check a pack folder and store it as the next version")
    load.add_argument("folder", nargs="?", type=Path, help="the pack folder (default: CLIENT_DIR)")
    load.add_argument("--org", help="the organisation (default: ORG_ID, or the folder's name)")
    out = commands.add_parser("export", help="write a stored version back out as a pack folder")
    out.add_argument("org")
    out.add_argument("folder", type=Path)
    out.add_argument("--version", type=int, help="the version to export (default: the newest)")
    listing = commands.add_parser("versions", help="list an organisation's stored versions")
    listing.add_argument("org")
    resend = commands.add_parser("send-device-config", help="send the newest version's DEVICE_CONFIG (§7.7)")
    resend.add_argument("org", nargs="?", help="the organisation (default: ORG_ID, or CLIENT_DIR's name)")
    args = parser.parse_args(argv)

    settings = load_settings()
    engine = create_db_engine(settings)
    try:
        if args.command == "import":
            folder = args.folder or settings.client_dir
            result = import_folder(engine, folder, args.org or settings.org or None)
            what = f"stored as version {result.version}" if result.changed else f"unchanged, still version {result.version}"
            print(f"{result.org_id}/{result.site_id} from {folder}: {what}; farm loaded from layout.json")
        elif args.command == "export":
            pack = export_pack(engine, args.org, args.folder, args.version)
            print(f"{args.org} version {pack.version}: {len(pack.files)} files written to {args.folder}")
        elif args.command == "versions":
            for row in versions(engine, args.org):
                sent = row.device_config_sent_at.isoformat() if row.device_config_sent_at else "not yet"
                print(f"{row.version}\t{row.imported_at.isoformat()}\t{row.imported_from or ''}\tDEVICE_CONFIG sent: {sent}")
        else:
            from ..api.commands.publish import publisher_for
            from ..api.device_config import send

            pack = stored(engine, args.org or settings.org_id)
            count = send(engine, pack, publisher_for(settings))
            print(f"{pack.org_id} version {pack.version}: DEVICE_CONFIG sent to {count} zone controller(s)")
    except Refused as exc:
        print(f"{args.command}: the pack was not stored:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    except (LookupError, FileNotFoundError) as exc:
        print(f"{args.command}: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
