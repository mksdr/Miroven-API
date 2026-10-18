"""TrueNAS 25.10 native Custom App installer and pull-based release deployer.

Standard library only. Run as root on the NAS, never inside an application container.
The only downloaded deployment input is a validated image digest, never shell code.

Workflow:
  1. Create a Generic/POSIX ZFS dataset (e.g. tank/miroven -> /mnt/tank/miroven).
  2. Run `python3 manage.py init` to create directories, deploy files, and secret templates.
  3. Edit `<root>/secrets/api.env` (domains and SMTP) and ensure `secrets/tunnel-token` is set
     (Cloudflare Tunnel route -> HTTP `proxy:8080`).
  4. Run `python3 <root>/deploy/manage.py check` then `install`.
  5. Schedule cron jobs for `poll` (release updates), `backup` (offline ZFS snapshot), and
     `prune` (expire `miroven-auto-*` snapshots older than 7 days). Use `retry` after fixing
     a blocked pending deployment.
"""

import argparse
import getpass
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


def _default_dataset():
    env_dataset = os.environ.get("MIROVEN_DATASET")
    if env_dataset:
        return env_dataset
    resolved = Path(__file__).resolve()
    if resolved.is_relative_to("/mnt"):
        if resolved.parent.name == "deploy":
            dataset = resolved.parents[1].relative_to("/mnt").as_posix()
            if "/" in dataset:
                return dataset
        if resolved.parts[-3:] == ("deploy", "truenas", "manage.py") and len(resolved.parents) > 3:
            dataset = resolved.parents[3].relative_to("/mnt").as_posix()
            if "/" in dataset:
                return dataset
    return "tank/miroven"


DATASET = _default_dataset()
ROOT = Path(os.environ.get("MIROVEN_ROOT", f"/mnt/{DATASET}"))
APP = os.environ.get("MIROVEN_APP", "miroven")
REPOSITORY = os.environ.get("MIROVEN_REPOSITORY", "mksdr/Miroven-API")
IMAGE = os.environ.get("MIROVEN_IMAGE", f"ghcr.io/{REPOSITORY.lower()}")
STATE = ROOT / "state"
STAGE = "preflight"


def run(*args, timeout=1800):
    result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        # Middleware/Docker error messages may contain credentials: never echo them.
        raise RuntimeError("Host command failed; inspect the NAS privately")
    return result.stdout.decode().strip()


def middleware(method, *args, job=False):
    return run("midclt", "call", *(["-j"] if job else []), method, *args)


def app_entry():
    entries = json.loads(middleware("app.query", json.dumps([["id", "=", APP]])))
    return entries[0] if entries else None


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def validate_manifest(value):
    if (
        value.get("schema") != 1
        or not re.fullmatch(r"v\d+\.\d+\.\d+", value.get("tag", ""))
        or not re.fullmatch(r"[0-9a-f]{40}", value.get("commit", ""))
        or not re.fullmatch(re.escape(IMAGE) + r"@sha256:[0-9a-f]{64}", value.get("image", ""))
    ):
        raise ValueError("Invalid release descriptor")
    return value


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "miroven-truenas-deployer"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ValueError("No published GitHub release found; push a vX.Y.Z tag first") from None
        raise
    if len(data) > 1024 * 1024:
        raise ValueError("Release response too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def latest_release():
    release = fetch_json(f"https://api.github.com/repos/{REPOSITORY}/releases/latest")
    tag = release["tag_name"]
    if (
        release.get("draft")
        or release.get("prerelease")
        or not re.fullmatch(r"v\d+\.\d+\.\d+", tag)
    ):
        raise ValueError("Only stable semantic version releases are deployable")
    manifest = validate_manifest(
        fetch_json(f"https://github.com/{REPOSITORY}/releases/download/{tag}/deployment.json")
    )
    if manifest["tag"] != tag:
        raise ValueError("Release tag mismatch")
    return manifest


def compose_for(manifest):
    config = json.loads((ROOT / "deploy" / "compose.template.json").read_text(encoding="utf-8"))
    root = str(ROOT)
    for service in config["services"].values():
        for item in service.get("env_file", ()):
            if item["path"].startswith("/mnt/tank/miroven/"):
                item["path"] = root + item["path"].removeprefix("/mnt/tank/miroven")
        for volume in service.get("volumes", ()):
            if volume["source"].startswith("/mnt/tank/miroven/"):
                volume["source"] = root + volume["source"].removeprefix("/mnt/tank/miroven")
    for name in ("api", "worker", "migrate"):
        config["services"][name]["image"] = manifest["image"]
    # Force the one-shot migration container to be recreated for every release.
    config["services"]["migrate"]["labels"] = {"org.miroven.release": manifest["tag"]}
    return config


def preflight():
    if not re.fullmatch(r"[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)+", DATASET):
        raise ValueError("Invalid ZFS dataset name")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+", REPOSITORY):
        raise ValueError("Invalid GitHub repository name")
    if run("zfs", "list", "-H", "-o", "mountpoint", DATASET) != str(ROOT):
        raise ValueError(f"Create the dedicated {DATASET} ZFS dataset first")
    for name in ("postgres", "blobs", "secrets", "deploy", "state"):
        path = ROOT / name
        if not path.is_dir() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("Required dataset directory is missing or outside the application")
    values = {}
    for name in ("api.env", "db.env"):
        path = ROOT / "secrets" / name
        if not path.is_file() or path.stat().st_mode & 0o077 or path.stat().st_uid != 0:
            raise ValueError("Environment files must be root-owned and mode 0600")
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"Invalid line in {name}")
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if "REPLACE" in value or "example.com" in value:
                raise ValueError("Replace example environment values")
            values[key] = value
    password = values.get("POSTGRES_PASSWORD", "")
    if not re.fullmatch(r"[0-9a-f]{48,}", password):
        raise ValueError("Use a generated hex database password")
    expected = f"postgresql+psycopg://miroven:{password}@db:5432/miroven"
    if values.get("MIROVEN_DATABASE_URL") != expected:
        raise ValueError("Database credentials do not match")
    if not re.fullmatch(r"[0-9a-f]{64,}", values.get("MIROVEN_SECRET_KEY", "")):
        raise ValueError("Use a generated hex secret key")
    for key in ("MIROVEN_PUBLIC_ORIGIN", "MIROVEN_API_ORIGIN"):
        origin = values.get(key, "")
        match = re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::([1-9]\d{0,4}))?", origin)
        if not match or (match.group(1) and int(match.group(1)) > 65535):
            raise ValueError("Set HTTPS origins without a trailing slash")
    token = ROOT / "secrets" / "tunnel-token"
    if not token.is_file() or not token.read_text(encoding="utf-8").strip():
        raise ValueError("Create the tunnel token file")
    if token.stat().st_uid != 65532 or token.stat().st_mode & 0o077:
        raise ValueError("Tunnel token must be owned by UID 65532 and mode 0400")
    if not (ROOT / "deploy" / "Caddyfile").is_file():
        raise ValueError("Missing tunnel Caddyfile")
    return values["MIROVEN_API_ORIGIN"]


def snapshot():
    name = f"{DATASET}@miroven-auto-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}"
    run("zfs", "snapshot", "-r", name)
    return name


def wait_healthy(origin):
    # Probe the real API container and worker, then the public Tunnel path.
    for _ in range(60):
        ids = run(
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project=ix-{APP}",
            "--filter",
            "label=com.docker.compose.service=api",
        ).splitlines()
        workers = run(
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project=ix-{APP}",
            "--filter",
            "label=com.docker.compose.service=worker",
        ).splitlines()
        if len(ids) == 1 and len(workers) == 1:
            health = run("docker", "inspect", "--format", "{{.State.Health.Status}}", ids[0])
            if health == "healthy":
                try:
                    urllib.request.urlopen(origin + "/api/v1/auth/session", timeout=10).close()
                except urllib.error.HTTPError as response:
                    if (
                        response.code == 401
                        and response.headers.get("Cache-Control") == "no-store"
                        and json.loads(response.read(65536)).get("code") == "unauthenticated"
                    ):
                        return
                except (OSError, ValueError):
                    pass
        time.sleep(5)
    raise RuntimeError("Readiness, worker or public Tunnel check failed")


def apply_pending(origin):
    global STAGE
    pending = json.loads((STATE / "pending.json").read_text())
    STAGE = "apply"
    entry = app_entry()
    if entry:
        if entry["state"] != "STOPPED":
            middleware("app.stop", APP, job=True)
        middleware(
            "app.update", APP, json.dumps({"custom_compose_config": pending["config"]}), job=True
        )
    else:
        middleware(
            "app.create",
            json.dumps(
                {
                    "app_name": APP,
                    "custom_app": True,
                    "custom_compose_config": pending["config"],
                }
            ),
            job=True,
        )
    if app_entry()["state"] != "RUNNING":
        middleware("app.start", APP, job=True)
    STAGE = "health"
    wait_healthy(origin)
    write_json(STATE / "current.json", pending)
    (STATE / "pending.json").unlink()
    print(f"deployed:{pending['manifest']['tag']}")


def deploy(install, origin):
    global STAGE
    if (STATE / "pending.json").exists():
        raise RuntimeError("Unfinished deployment: inspect and use retry explicitly")
    STAGE = "release"
    manifest = latest_release()
    entry = app_entry()
    if install:
        if entry or (STATE / "current.json").exists():
            raise ValueError("Application already exists")
    else:
        if not entry or entry["state"] != "RUNNING":
            raise ValueError("Application is not running; refusing unattended startup")
        current = json.loads((STATE / "current.json").read_text())
        if current["manifest"]["image"] == manifest["image"]:
            return
        old_version = tuple(map(int, current["manifest"]["tag"][1:].split(".")))
        new_version = tuple(map(int, manifest["tag"][1:].split(".")))
        if new_version <= old_version:
            raise ValueError("Refusing an unattended downgrade or reused version")
    STAGE = "pull"
    run("docker", "pull", manifest["image"])
    pending = {"manifest": manifest, "config": compose_for(manifest), "snapshot": None}
    if not install:
        pending["previous"] = current["manifest"]
    write_json(STATE / "pending.json", pending)
    if not install:
        STAGE = "snapshot"
        middleware("app.stop", APP, job=True)
        # Includes PostgreSQL, blobs, secrets and previous deployment state, offline.
        pending["snapshot"] = snapshot()
        write_json(STATE / "pending.json", pending)
    apply_pending(origin)


def backup(origin):
    global STAGE
    if (STATE / "pending.json").exists() or not (STATE / "current.json").exists():
        raise ValueError("Resolve the deployment before backing up")
    entry = app_entry()
    if not entry or entry["state"] != "RUNNING":
        raise ValueError("Backup expects a running application")
    STAGE = "backup"
    middleware("app.stop", APP, job=True)
    try:
        name = snapshot()
        print(f"snapshot:{name}")
    finally:
        middleware("app.start", APP, job=True)
    wait_healthy(origin)


def prune():
    # Only our exact snapshot names, inside this dedicated dataset and its children.
    if (STATE / "pending.json").exists():
        raise ValueError("Resolve the deployment before expiring recovery snapshots")
    snapshots = run(
        "zfs",
        "list",
        "-H",
        "-p",
        "-t",
        "filesystem,snapshot",
        "-o",
        "name,creation",
        "-r",
        DATASET,
    )
    cutoff = time.time() - 7 * 86400
    for line in snapshots.splitlines():
        name, created = line.split("\t")
        if "@" not in name:
            continue
        dataset, suffix = name.split("@", 1)
        if (
            (dataset == DATASET or dataset.startswith(DATASET + "/"))
            and re.fullmatch(r"miroven-auto-\d{8}T\d{12}Z", suffix)
            and int(created) < cutoff
        ):
            run("zfs", "destroy", name)
            print(f"expired_snapshot:{name}")


def init(template_dir=None):
    if not re.fullmatch(r"[a-zA-Z0-9._-]+(?:/[a-zA-Z0-9._-]+)+", DATASET):
        raise ValueError("Invalid ZFS dataset name")
    if run("zfs", "list", "-H", "-o", "mountpoint", DATASET) != str(ROOT):
        raise ValueError(f"Create the dedicated {DATASET} ZFS dataset first")
    if not ROOT.is_dir() or ROOT.is_symlink():
        raise ValueError("Dataset root directory is missing or a symlink")
    os.chown(ROOT, 0, 0)
    os.chmod(ROOT, 0o700)
    for name, uid, gid in (
        ("postgres", 0, 0),
        ("blobs", 10001, 10001),
        ("secrets", 0, 0),
        ("deploy", 0, 0),
        ("state", 0, 0),
    ):
        path = ROOT / name
        if path.is_symlink():
            raise ValueError("Required dataset directory must not be a symlink")
        path.mkdir(mode=0o700, exist_ok=True)
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError("Required dataset directory is missing or outside the application")
        os.chown(path, uid, gid)
        os.chmod(path, 0o700)
    source_dir = Path(template_dir or Path(__file__).resolve().parent)
    if not (source_dir / "compose.template.json").is_file():
        source_dir = ROOT / "source" / "deploy" / "truenas"
    for filename, mode in (
        ("manage.py", 0o700),
        ("compose.template.json", 0o600),
        ("Caddyfile", 0o644),
    ):
        src = source_dir / filename
        dst = ROOT / "deploy" / filename
        if not src.is_file():
            raise ValueError(f"Missing template file: {filename}")
        if src.resolve() != dst.resolve():
            shutil.copyfile(src, dst)
        os.chown(dst, 0, 0)
        os.chmod(dst, mode)
    db_env = ROOT / "secrets" / "db.env"
    api_env = ROOT / "secrets" / "api.env"
    if not db_env.exists() and not api_env.exists():
        password = secrets.token_hex(24)
        key = secrets.token_hex(32)
        for name, dst in (("db.env", db_env), ("api.env", api_env)):
            example = source_dir / (name + ".example")
            if not example.is_file():
                raise ValueError(f"Missing template file: {name}.example")
            text = example.read_text(encoding="utf-8")
            text = text.replace("REPLACE_WITH_RANDOM_64_HEX", key)
            text = text.replace("REPLACE_WITH_RANDOM_HEX", password)
            with dst.open("x", encoding="utf-8") as stream:
                stream.write(text)
            os.chown(dst, 0, 0)
            os.chmod(dst, 0o600)
    elif db_env.exists() != api_env.exists():
        raise ValueError("Incomplete secret environment files; inspect secrets directory")
    token_path = ROOT / "secrets" / "tunnel-token"
    if not token_path.exists() and sys.stdin.isatty():
        token = getpass.getpass("Cloudflare Tunnel token (leave blank to skip): ").strip()
        if token:
            with token_path.open("x", encoding="utf-8") as stream:
                stream.write(token + "\n")
            os.chown(token_path, 65532, 65532)
            os.chmod(token_path, 0o400)
    print("initialized")


def main():
    global STAGE
    STAGE = "preflight"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=["init", "install", "poll", "retry", "backup", "prune", "check"],
    )
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Run as root on TrueNAS")
    import fcntl

    os.umask(0o077)
    try:
        if args.action == "init":
            STAGE = "init"
            init()
            return 0
        if not STATE.is_dir() or not STATE.resolve().is_relative_to(ROOT):
            preflight()
        with (STATE / "deploy.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
            origin = preflight()
            if args.action == "check":
                latest_release()
                print("preflight_complete")
            elif args.action in ("install", "poll"):
                deploy(args.action == "install", origin)
            elif args.action == "retry":
                pending = json.loads((STATE / "pending.json").read_text(encoding="utf-8"))
                validate_manifest(pending["manifest"])
                if "previous" in pending and not pending.get("snapshot"):
                    raise ValueError("No completed pre-deployment snapshot; recover manually")
                apply_pending(origin)
            elif args.action == "backup":
                backup(origin)
            else:
                prune()
    except Exception as exc:
        print(f"operation_failed:{STAGE}:{type(exc).__name__}", file=sys.stderr)
        if type(exc) is ValueError:
            print(str(exc), file=sys.stderr)
        print(
            "Check the NAS privately. If pending.json exists, automatic deployment is blocked.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
