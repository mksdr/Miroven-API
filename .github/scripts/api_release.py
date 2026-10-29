"""Complete a tested API tag's draft release without replacing published artifacts."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from release_policy import STABLE_VERSION, collect_versions, validate_versions


def gh(*arguments: str) -> str:
    return subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def get_release(repository: str, tag: str) -> dict | None:
    try:
        return json.loads(gh("api", f"repos/{repository}/releases/tags/{tag}"))
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return None
        raise


def latest_flag(repository: str, tag: str) -> str:
    try:
        latest = json.loads(gh("api", f"repos/{repository}/releases/latest"))
    except subprocess.CalledProcessError as error:
        if "HTTP 404" in (error.stderr or ""):
            return "--latest"
        raise
    previous = latest["tag_name"]
    if not previous.startswith("v") or not STABLE_VERSION.fullmatch(previous[1:]):
        raise ValueError("Latest release is not a stable version; review it before publishing")
    previous_version = tuple(map(int, previous[1:].split(".")))
    new_version = tuple(map(int, tag[1:].split(".")))
    return "--latest=false" if previous_version > new_version else "--latest"


def validate_descriptor(descriptor: dict, repository: str, tag: str, commit: str) -> None:
    image_pattern = re.escape(f"ghcr.io/{repository.lower()}@sha256:") + r"[0-9a-f]{64}"
    if (
        descriptor.get("schema") != 1
        or descriptor.get("tag") != tag
        or descriptor.get("commit") != commit
        or not re.fullmatch(image_pattern, descriptor.get("image", ""))
    ):
        raise ValueError("Deployment descriptor does not match the tested tag, commit and image")


def already_published(repository: str, tag: str, commit: str) -> bool:
    release = get_release(repository, tag)
    if release is None or release["draft"]:
        return False
    if release["prerelease"] or not any(
        asset["name"] == "deployment.json" for asset in release["assets"]
    ):
        raise ValueError("Published stable release is incomplete; refusing to overwrite it")
    descriptor = json.loads(
        gh(
            "release",
            "download",
            tag,
            "--repo",
            repository,
            "--pattern",
            "deployment.json",
            "--output",
            "-",
        )
    )
    validate_descriptor(descriptor, repository, tag, commit)
    return True


def publish(repository: str, tag: str, commit: str, digest: str, output: Path) -> None:
    # A successful rerun must not rebuild/rewrite the immutable public descriptor.
    if already_published(repository, tag, commit):
        return
    descriptor = {
        "schema": 1,
        "image": f"ghcr.io/{repository.lower()}@{digest}",
        "commit": commit,
        "tag": tag,
    }
    validate_descriptor(descriptor, repository, tag, commit)
    latest = latest_flag(repository, tag)
    release = get_release(repository, tag)
    if release is None:
        # Retain support for manually pushed, verified stable tags.
        gh(
            "release",
            "create",
            tag,
            "--repo",
            repository,
            "--verify-tag",
            "--draft",
            "--generate-notes",
        )
    elif release["prerelease"]:
        raise ValueError("Stable deployment cannot publish a prerelease")
    output.write_text(json.dumps(descriptor, indent=2) + "\n", encoding="utf-8", newline="\n")
    # Only a draft can reach here; retain Release Please's generated release notes.
    gh("release", "upload", tag, str(output), "--repo", repository, "--clobber")
    # Publishing is the last operation: TrueNAS must never see a release without its asset.
    gh("release", "edit", tag, "--repo", repository, "--draft=false", latest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["inspect", "publish"])
    args = parser.parse_args()
    repository = os.environ["GITHUB_REPOSITORY"]
    tag = os.environ["RELEASE_TAG"]
    commit = os.environ["GITHUB_SHA"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    if not tag.startswith("v") or not STABLE_VERSION.fullmatch(tag[1:]):
        raise ValueError("Only stable vX.Y.Z tags are publishable")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected the full tested commit SHA")
    validate_versions(collect_versions(Path(__file__).resolve().parents[2]), tag)
    if args.operation == "inspect":
        published = already_published(repository, tag, commit)
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"published={str(published).lower()}\n")
    else:
        publish(repository, tag, commit, os.environ["IMAGE_DIGEST"], Path("deployment.json"))


if __name__ == "__main__":
    main()
