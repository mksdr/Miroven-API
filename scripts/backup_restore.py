"""Offline, paired DB/local-blob backups. Restore only into empty destinations."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath


def checksum(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def validate_backup(folder):
    manifest = json.loads((folder / "manifest.json").read_text())
    if manifest.get("format") != 1 or set(manifest.get("files", {})) != {"db.dump", "blobs.tar"}:
        raise ValueError("Unsupported backup manifest")
    for name, expected in manifest["files"].items():
        if checksum(folder / name) != expected:
            raise ValueError("Backup checksum mismatch")
    with tarfile.open(folder / "blobs.tar") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe blob archive")
    return manifest


class Compose:
    def __init__(self, files, project=None, env_file=None):
        self.base = ["docker", "compose"]
        if env_file:
            self.base += ["--env-file", env_file]
        if project:
            self.base += ["-p", project]
        for file in files:
            self.base += ["-f", file]

    def run(self, *args, output=None, source=None):
        result = subprocess.run(
            [*self.base, *args],
            stdin=source,
            stdout=output or subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode:
            # Compose stderr can contain interpolated credentials. Do not echo it.
            raise RuntimeError("Compose operation failed; inspect the service privately")
        return result.stdout or b""

    def db(self, command, output=None, source=None):
        return self.run("exec", "-T", "db", "sh", "-c", command, output=output, source=source)

    def blob(self, *command, output=None, source=None):
        return self.run(
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "--entrypoint",
            command[0],
            "api",
            *command[1:],
            output=output,
            source=source,
        )

    def require_local(self):
        # Config output is read in memory only: never print interpolated secrets.
        config = json.loads(self.run("config", "--format", "json"))
        environment = config["services"]["api"]["environment"]
        if environment.get("MIROVEN_BLOB_DIRECTORY") != "/data/blobs":
            raise ValueError("This backup tool requires the /data/blobs mount")
        if (
            config["services"]["api"]["environment"].get("MIROVEN_STORAGE_BACKEND", "local")
            != "local"
        ):
            raise ValueError("This backup tool requires local storage")


def backup(compose, folder):
    compose.require_local()
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    running = compose.run("ps", "--status", "running", "--services").decode().split()
    stopped = [service for service in ("api", "worker") if service in running]
    try:
        if stopped:
            compose.run("stop", "-t", "60", *stopped)
        # No other API replicas, workers, migrations or external writers may run.
        with (folder / "db.dump").open("wb") as target:
            compose.db('pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc', output=target)
        with (folder / "blobs.tar").open("wb") as target:
            compose.blob("tar", "-C", "/data/blobs", "-cf", "-", ".", output=target)
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "files": {name: checksum(folder / name) for name in ("db.dump", "blobs.tar")},
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2))
        validate_backup(folder)
    finally:
        if stopped:
            compose.run("start", *stopped)


def restore(compose, folder):
    compose.require_local()
    validate_backup(folder)
    running = compose.run("ps", "--status", "running", "--services").decode().split()
    if "api" in running or "worker" in running:
        raise ValueError("Stop API and worker before restoring")
    count = compose.db(
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc '
        "\"SELECT count(*) FROM pg_tables WHERE schemaname='public'\""
    ).strip()
    if count != b"0":
        raise ValueError("Restore requires an empty database")
    compose.blob(
        "python",
        "-c",
        "from pathlib import Path; "
        "p=Path('/data/blobs'); assert not any(p.iterdir()), 'Blob volume must be empty'",
    )
    with (folder / "blobs.tar").open("rb") as source:
        compose.blob("tar", "-C", "/data/blobs", "-xf", "-", source=source)
    with (folder / "db.dump").open("rb") as source:
        compose.db(
            'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" '
            "--no-owner --no-acl --exit-on-error --single-transaction",
            source=source,
        )
    # A partial failure remains offline; never auto-start writers after restore.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("-f", "--file", action="append", default=[])
    parser.add_argument("-p", "--project")
    parser.add_argument("--env-file")
    args = parser.parse_args()
    try:
        if args.action == "verify":
            validate_backup(args.folder)
        else:
            compose = Compose(args.file or ["compose.yaml"], args.project, args.env_file)
            {"backup": backup, "restore": restore}[args.action](compose, args.folder)
    except Exception as exc:
        print(f"operation_failed:{type(exc).__name__}", file=sys.stderr)
        return 1
    print(f"{args.action}_complete")
    return 0


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
