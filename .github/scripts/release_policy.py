"""Validate PR titles and synchronized stable release metadata without app dependencies."""

import argparse
import ast
import json
import os
import re
import tomllib
from pathlib import Path

STABLE_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
TITLE = re.compile(
    r"(?:feat|fix|perf|refactor|docs|test|build|ci|chore|revert)"
    r"(?:\([a-z0-9][a-z0-9._/-]*\))?!?: \S(?:[^\r\n]*\S)?"
)


def validate_title(title: str) -> None:
    if not TITLE.fullmatch(title):
        raise ValueError(
            "Use type(scope): summary or type(scope)!: summary; scope is optional. "
            "See CONTRIBUTING.md. Do not add leading/trailing whitespace or newlines."
        )


def collect_versions(root: Path) -> dict[str, str]:
    def read_json(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    versions = {
        "version.txt": (root / "version.txt").read_text(encoding="utf-8").strip(),
        "release manifest": read_json(".release-please-manifest.json")["."],
    }
    package_path = "web/package.json" if (root / "web/package.json").is_file() else "package.json"
    package = read_json(package_path)
    lock = read_json(package_path.replace("package.json", "package-lock.json"))
    versions["package.json"] = package["version"]
    versions["lockfile version"] = lock["version"]
    versions["lockfile root package"] = lock["packages"][""]["version"]
    if (root / "pyproject.toml").is_file():
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        versions["pyproject.toml"] = project["project"]["version"]
        lockfile = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
        own_packages = [
            item
            for item in lockfile["package"]
            if item["name"] == project["project"]["name"] and item.get("source") == {"virtual": "."}
        ]
        if len(own_packages) != 1:
            raise ValueError("Expected exactly one local project in uv.lock")
        versions["uv.lock project"] = own_packages[0]["version"]
        tree = ast.parse((root / "app/main.py").read_text(encoding="utf-8"))
        app_versions = [
            ast.literal_eval(keyword.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "FastAPI"
            for keyword in node.keywords
            if keyword.arg == "version"
        ]
        if len(app_versions) != 1:
            raise ValueError("Expected exactly one FastAPI version")
        versions["FastAPI"] = app_versions[0]
        versions["OpenAPI metadata"] = read_json("contracts/openapi.json")["info"]["version"]
    return versions


def validate_versions(versions: dict[str, str], tag: str | None = None) -> str:
    expected = versions["version.txt"]
    if not isinstance(expected, str) or not STABLE_VERSION.fullmatch(expected):
        raise ValueError("version.txt must contain a stable X.Y.Z version")
    mismatches = [name for name, value in versions.items() if value != expected]
    if mismatches:
        raise ValueError("Release version mismatch: " + ", ".join(mismatches))
    if tag is not None and tag != "v" + expected:
        raise ValueError("Tag must equal v plus the checked-out application version")
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Also verify the checked-out stable release tag")
    args = parser.parse_args()
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name in {"pull_request", "pull_request_target"}:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        validate_title(event["pull_request"]["title"])
        print("PR title follows Conventional Commits.")
    tag = args.tag
    if tag is None and os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ["GITHUB_REF_NAME"]
    root = Path(__file__).resolve().parents[2]
    version = validate_versions(collect_versions(root), tag)
    print(f"Release metadata agrees at {version}.")


if __name__ == "__main__":
    main()
