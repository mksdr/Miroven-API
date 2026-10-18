import json
import time

import pytest

from deploy.truenas import manage


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setattr(manage, "STATE", tmp_path)
    return tmp_path


@pytest.fixture
def release():
    return {
        "schema": 1,
        "tag": "v0.2.0",
        "commit": "a" * 40,
        "image": manage.IMAGE + "@sha256:" + "b" * 64,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 2),
        ("tag", "v0.2.0-rc1"),
        ("tag", "../../other"),
        ("commit", "main"),
        ("image", "ghcr.io/other/api@sha256:" + "b" * 64),
        ("image", manage.IMAGE + ":latest"),
    ],
)
def test_descriptor_rejects_untrusted_images_and_unstable_tags(release, field, value):
    release[field] = value
    with pytest.raises(ValueError):
        manage.validate_manifest(release)


def test_pending_deployment_blocks_even_release_fetch(host, monkeypatch):
    (host / "pending.json").write_text("{}")
    monkeypatch.setattr(manage, "latest_release", lambda: pytest.fail("Must not fetch"))
    with pytest.raises(RuntimeError, match="Unfinished"):
        manage.deploy(False, "https://api.example.com")


def test_update_pulls_then_stops_then_snapshots_before_applying(host, release, monkeypatch):
    previous = dict(release, tag="v0.1.0", image=manage.IMAGE + "@sha256:" + "c" * 64)
    manage.write_json(host / "current.json", {"manifest": previous})
    events = []
    monkeypatch.setattr(manage, "latest_release", lambda: release)
    monkeypatch.setattr(manage, "app_entry", lambda: {"state": "RUNNING"})
    monkeypatch.setattr(manage, "compose_for", lambda _: {"services": {}})
    monkeypatch.setattr(manage, "run", lambda *args: events.append(args[1]))
    monkeypatch.setattr(manage, "middleware", lambda method, *args, **kw: events.append(method))

    def snapshot():
        events.append("snapshot")
        assert (host / "pending.json").exists()
        return "tank/miroven@test"

    def apply(origin):
        events.append("apply")
        pending = json.loads((host / "pending.json").read_text())
        assert pending["snapshot"] == "tank/miroven@test"
        assert pending["previous"] == previous

    monkeypatch.setattr(manage, "snapshot", snapshot)
    monkeypatch.setattr(manage, "apply_pending", apply)
    manage.deploy(False, "https://api.example.com")
    assert events == ["pull", "app.stop", "snapshot", "apply"]


def test_snapshot_failure_never_applies_and_preserves_pending(host, release, monkeypatch):
    previous = dict(release, tag="v0.1.0", image=manage.IMAGE + "@sha256:" + "c" * 64)
    manage.write_json(host / "current.json", {"manifest": previous})
    monkeypatch.setattr(manage, "latest_release", lambda: release)
    monkeypatch.setattr(manage, "app_entry", lambda: {"state": "RUNNING"})
    monkeypatch.setattr(manage, "compose_for", lambda _: {})
    monkeypatch.setattr(manage, "run", lambda *args: "")
    monkeypatch.setattr(manage, "middleware", lambda *args, **kw: "")
    monkeypatch.setattr(manage, "apply_pending", lambda _: pytest.fail("Must not update"))

    def fail_snapshot():
        raise RuntimeError("No space")

    monkeypatch.setattr(manage, "snapshot", fail_snapshot)
    with pytest.raises(RuntimeError, match="No space"):
        manage.deploy(False, "https://api.example.com")
    assert json.loads((host / "pending.json").read_text())["snapshot"] is None
    assert json.loads((host / "current.json").read_text())["manifest"] == previous


def test_failed_public_health_keeps_old_current_and_new_pending(host, release, monkeypatch):
    manage.write_json(host / "current.json", {"manifest": {"tag": "v0.1.0"}})
    manage.write_json(host / "pending.json", {"manifest": release, "config": {}})
    monkeypatch.setattr(manage, "app_entry", lambda: {"state": "RUNNING"})
    monkeypatch.setattr(manage, "middleware", lambda *args, **kw: "")

    def fail_health(origin):
        raise RuntimeError("Tunnel down")

    monkeypatch.setattr(manage, "wait_healthy", fail_health)
    with pytest.raises(RuntimeError, match="Tunnel down"):
        manage.apply_pending("https://api.example.com")
    assert (host / "pending.json").exists()
    assert json.loads((host / "current.json").read_text())["manifest"]["tag"] == "v0.1.0"


def test_unattended_downgrade_is_rejected_before_pull(host, release, monkeypatch):
    manage.write_json(host / "current.json", {"manifest": dict(release, tag="v0.3.0", image="old")})
    monkeypatch.setattr(manage, "latest_release", lambda: release)
    monkeypatch.setattr(manage, "app_entry", lambda: {"state": "RUNNING"})
    monkeypatch.setattr(manage, "run", lambda *args: pytest.fail("Must not pull"))
    with pytest.raises(ValueError, match="downgrade"):
        manage.deploy(False, "https://api.example.com")


def test_prune_only_expires_old_exact_names_inside_dataset(host, monkeypatch):
    old = int(time.time() - 8 * 86400)
    new = int(time.time())
    suffix = "miroven-auto-20260101T010203123456Z"
    listing = "\n".join(
        [
            f"tank/miroven\t{old}",
            f"tank/miroven@{suffix}\t{old}",
            f"tank/miroven/blobs@{suffix}\t{old}",
            f"tank/miroven@manual-backup\t{old}",
            f"tank/miroven-other@{suffix}\t{old}",
            f"tank/miroven@miroven-auto-20260102T010203123456Z\t{new}",
        ]
    )
    destroyed = []

    def run(*args):
        if args[1] == "list":
            return listing
        destroyed.append(args)

    monkeypatch.setattr(manage, "run", run)
    manage.prune()
    assert destroyed == [
        ("zfs", "destroy", f"tank/miroven@{suffix}"),
        ("zfs", "destroy", f"tank/miroven/blobs@{suffix}"),
    ]


def test_pending_recovery_snapshot_is_not_pruned(host, monkeypatch):
    (host / "pending.json").write_text("{}")
    monkeypatch.setattr(manage, "run", lambda *args: pytest.fail("Must not delete snapshots"))
    with pytest.raises(ValueError, match="recovery snapshots"):
        manage.prune()


def test_compose_for_rewrites_custom_root_paths(tmp_path, release, monkeypatch):
    from pathlib import Path

    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    template = Path(__file__).resolve().parents[1] / "deploy" / "truenas" / "compose.template.json"
    (deploy_dir / "compose.template.json").write_text(
        template.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    config = manage.compose_for(release)
    for service in config["services"].values():
        for item in service.get("env_file", ()):
            assert item["path"].startswith(str(tmp_path))
            assert "/mnt/tank/miroven" not in item["path"]
        for volume in service.get("volumes", ()):
            assert volume["source"].startswith(str(tmp_path))
            assert "/mnt/tank/miroven" not in volume["source"]


def test_preflight_accepts_whitespace_comments_and_custom_https_ports(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    for name in ("postgres", "blobs", "secrets", "deploy", "state"):
        (tmp_path / name).mkdir()
    (tmp_path / "deploy" / "Caddyfile").write_text(":8080 {}\n", encoding="utf-8")
    (tmp_path / "secrets" / "tunnel-token").write_text("token-value\n", encoding="utf-8")
    pw = "a" * 48
    key = "b" * 64
    (tmp_path / "secrets" / "db.env").write_text(
        f"  # indented comment\n   \nPOSTGRES_PASSWORD={pw}\n",
        encoding="utf-8",
    )
    api_env = tmp_path / "secrets" / "api.env"
    api_env.write_text(
        "\n".join(
            [
                "   ",
                "  # comment",
                f"MIROVEN_DATABASE_URL=postgresql+psycopg://miroven:{pw}@db:5432/miroven",
                f"MIROVEN_SECRET_KEY={key}",
                "MIROVEN_PUBLIC_ORIGIN=https://journal.valid.test:8443",
                "MIROVEN_API_ORIGIN=https://api.valid.test:8443",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    monkeypatch.setattr(manage, "DATASET", "storage/miroven")
    monkeypatch.setattr(manage, "run", lambda *args: str(tmp_path))
    real_stat = type(tmp_path).stat

    def fake_stat(self, *args, **kwargs):
        st = real_stat(self, *args, **kwargs)
        uid = 65532 if self.name == "tunnel-token" else 0
        mode = 0o100400 if self.name == "tunnel-token" else 0o100600
        return SimpleNamespace(
            st_mode=mode if stat.S_ISREG(st.st_mode) else st.st_mode,
            st_uid=uid,
        )

    monkeypatch.setattr(type(tmp_path), "stat", fake_stat)
    assert manage.preflight() == "https://api.valid.test:8443"

    api_env.write_text(
        api_env.read_text(encoding="utf-8").replace(":8443", ":70000"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="HTTPS origins"):
        manage.preflight()


def test_main_surfaces_value_error_but_hides_runtime_error_details(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(manage.sys, "platform", "linux")
    monkeypatch.setattr(manage.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setitem(
        manage.sys.modules,
        "fcntl",
        type("Fcntl", (), {"LOCK_EX": 1, "LOCK_NB": 2, "flock": staticmethod(lambda *a: None)}),
    )
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    monkeypatch.setattr(manage, "STATE", tmp_path / "state")
    monkeypatch.setattr(manage.sys, "argv", ["manage.py", "check"])
    monkeypatch.setattr(
        manage,
        "preflight",
        lambda: (_ for _ in ()).throw(ValueError("Replace example environment values")),
    )
    assert manage.main() == 1
    err = capsys.readouterr().err
    assert "operation_failed:preflight:ValueError" in err
    assert "Replace example environment values" in err

    (tmp_path / "state").mkdir()
    monkeypatch.setattr(
        manage,
        "preflight",
        lambda: (_ for _ in ()).throw(RuntimeError("secret-credential-in-stderr")),
    )
    assert manage.main() == 1
    err = capsys.readouterr().err
    assert "operation_failed:preflight:RuntimeError" in err
    assert "secret-credential-in-stderr" not in err


def test_missing_github_release_raises_clear_value_error(monkeypatch):
    import urllib.error

    def fail_open(request, timeout=30):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(manage.urllib.request, "urlopen", fail_open)
    with pytest.raises(ValueError, match="No published GitHub release found"):
        manage.latest_release()


def test_init_creates_layout_generates_matching_secrets_and_preserves_existing(
    tmp_path, monkeypatch, capsys
):
    chowns = {}
    chmods = {}
    monkeypatch.setattr(manage, "ROOT", tmp_path)
    monkeypatch.setattr(manage, "DATASET", "storage/miroven")
    monkeypatch.setattr(manage, "run", lambda *args: str(tmp_path))
    monkeypatch.setattr(
        manage.os, "chown", lambda p, u, g: chowns.__setitem__(p, (u, g)), raising=False
    )
    monkeypatch.setattr(manage.os, "chmod", lambda p, m: chmods.__setitem__(p, m))
    monkeypatch.setattr(manage.sys.stdin, "isatty", lambda: False)

    manage.init()
    out = capsys.readouterr().out
    assert out.strip() == "initialized"
    assert chowns[tmp_path / "blobs"] == (10001, 10001)
    assert (tmp_path / "deploy" / "manage.py").is_file()
    assert (tmp_path / "deploy" / "compose.template.json").is_file()
    assert (tmp_path / "deploy" / "Caddyfile").is_file()

    db_text = (tmp_path / "secrets" / "db.env").read_text(encoding="utf-8")
    api_text = (tmp_path / "secrets" / "api.env").read_text(encoding="utf-8")
    assert "REPLACE_WITH_RANDOM" not in db_text
    assert "REPLACE_WITH_RANDOM" not in api_text
    db_pw = [
        line.split("=", 1)[1]
        for line in db_text.splitlines()
        if line.startswith("POSTGRES_PASSWORD=")
    ][0]
    assert f"postgresql+psycopg://miroven:{db_pw}@db:5432/miroven" in api_text
    assert db_pw not in out

    monkeypatch.setattr(manage.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(manage.getpass, "getpass", lambda prompt: " my-tunnel-token \n")
    manage.init()
    assert (tmp_path / "secrets" / "db.env").read_text(encoding="utf-8") == db_text
    assert (tmp_path / "secrets" / "api.env").read_text(encoding="utf-8") == api_text
    assert (tmp_path / "secrets" / "tunnel-token").read_text(
        encoding="utf-8"
    ) == "my-tunnel-token\n"
    assert chowns[tmp_path / "secrets" / "tunnel-token"] == (65532, 65532)
    assert chmods[tmp_path / "secrets" / "tunnel-token"] == 0o400

    (tmp_path / "secrets" / "api.env").unlink()
    with pytest.raises(ValueError, match="Incomplete secret environment files"):
        manage.init()
