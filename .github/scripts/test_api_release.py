"""Exercise draft publication and retry safety without GitHub or Docker."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api_release

REPOSITORY = "synthetic/Miroven-API"
TAG = "v0.1.1"
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64
DESCRIPTOR = {
    "schema": 1,
    "image": f"ghcr.io/{REPOSITORY.lower()}@{DIGEST}",
    "commit": COMMIT,
    "tag": TAG,
}
DRAFT = {"draft": True, "prerelease": False, "assets": []}
PUBLISHED = {"draft": False, "prerelease": False, "assets": [{"name": "deployment.json"}]}


class ApiReleaseTests(unittest.TestCase):
    @patch.object(api_release, "gh")
    def test_older_delayed_release_does_not_replace_latest(self, gh):
        gh.return_value = json.dumps({"tag_name": "v0.2.0"})
        self.assertEqual(api_release.latest_flag(REPOSITORY, TAG), "--latest=false")
        gh.return_value = json.dumps({"tag_name": "v0.1.0"})
        self.assertEqual(api_release.latest_flag(REPOSITORY, TAG), "--latest")
        gh.side_effect = subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 404")
        self.assertEqual(api_release.latest_flag(REPOSITORY, TAG), "--latest")

    def test_descriptor_rejects_wrong_identity(self):
        api_release.validate_descriptor(DESCRIPTOR, REPOSITORY, TAG, COMMIT)
        for key, value in [
            ("schema", 2),
            ("tag", "v0.1.2"),
            ("commit", "c" * 40),
            ("image", "ghcr.io/other/api@" + DIGEST),
            ("image", "ghcr.io/example/api:latest"),
        ]:
            with self.subTest(field=key), self.assertRaises(ValueError):
                api_release.validate_descriptor({**DESCRIPTOR, key: value}, REPOSITORY, TAG, COMMIT)

    @patch.object(api_release, "gh")
    def test_draft_upload_precedes_publication_and_keeps_notes(self, gh):
        gh.return_value = json.dumps({"tag_name": "v0.1.0"})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "deployment.json"
            with patch.object(api_release, "get_release", return_value=DRAFT):
                api_release.publish(REPOSITORY, TAG, COMMIT, DIGEST, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), DESCRIPTOR)
        self.assertEqual(
            [call.args[:2] for call in gh.call_args_list if call.args[0] == "release"],
            [("release", "upload"), ("release", "edit")],
        )
        release_calls = [call for call in gh.call_args_list if call.args[0] == "release"]
        self.assertIn("--clobber", release_calls[0].args)
        self.assertNotIn("--notes", release_calls[1].args)

    @patch.object(api_release, "gh")
    def test_failed_upload_leaves_release_draft(self, gh):
        gh.side_effect = subprocess.CalledProcessError(1, ["gh"])
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(api_release, "get_release", return_value=DRAFT),
                patch.object(api_release, "latest_flag", return_value="--latest"),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    api_release.publish(REPOSITORY, TAG, COMMIT, DIGEST, Path(directory) / "d.json")
        self.assertEqual(len(gh.call_args_list), 1)
        self.assertEqual(gh.call_args.args[:2], ("release", "upload"))

    @patch.object(api_release, "gh")
    def test_published_rerun_does_not_write_or_publish(self, gh):
        gh.return_value = json.dumps(DESCRIPTOR)
        with patch.object(api_release, "get_release", return_value=PUBLISHED):
            api_release.publish(REPOSITORY, TAG, COMMIT, DIGEST, Path("must-not-be-created.json"))
        self.assertEqual(len(gh.call_args_list), 1)
        self.assertEqual(gh.call_args.args[:2], ("release", "download"))

    @patch.object(api_release, "gh")
    def test_incomplete_public_release_is_not_repaired_in_place(self, gh):
        with patch.object(api_release, "get_release", return_value={**PUBLISHED, "assets": []}):
            with self.assertRaises(ValueError):
                api_release.already_published(REPOSITORY, TAG, COMMIT)
        gh.assert_not_called()

    @patch.object(api_release, "gh")
    def test_manual_tag_creates_draft_before_asset(self, gh):
        gh.return_value = json.dumps({"tag_name": "v0.1.0"})
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(api_release, "get_release", return_value=None):
                api_release.publish(REPOSITORY, TAG, COMMIT, DIGEST, Path(directory) / "d.json")
        self.assertEqual(
            [call.args[:2] for call in gh.call_args_list if call.args[0] == "release"],
            [("release", "create"), ("release", "upload"), ("release", "edit")],
        )
        release_calls = [call for call in gh.call_args_list if call.args[0] == "release"]
        self.assertIn("--draft", release_calls[0].args)

    @patch.object(api_release, "gh")
    def test_only_not_found_is_treated_as_missing(self, gh):
        gh.side_effect = subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 404")
        self.assertIsNone(api_release.get_release(REPOSITORY, TAG))
        gh.side_effect = subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 403")
        with self.assertRaises(subprocess.CalledProcessError):
            api_release.get_release(REPOSITORY, TAG)


if __name__ == "__main__":
    unittest.main()
