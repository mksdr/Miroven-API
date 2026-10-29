"""Synthetic checks for release policy failure paths; no services or real data."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from release_policy import collect_versions, validate_title, validate_versions


class ReleasePolicyTests(unittest.TestCase):
    def test_titles(self):
        for title in [
            "fix(sync): preserve pending uploads",
            "feat!: change supported API",
            "chore(main): release 1.2.1",
            "chore(deps-dev): bump vitest to 5.0.5",
            "ci: update actions",
            "fix(auth): 로그인 오류 수정",
            'docs: describe "quotes" and $() literally',
        ]:
            with self.subTest(title=title):
                validate_title(title)

    def test_invalid_titles(self):
        for title in [
            "",
            "Add a feature",
            "Feat: uppercase",
            "fix:no space",
            "fix(): empty scope",
            "fix: ",
            "fix: trailing ",
            " fix: leading",
            "fix: first\nsecond",
            "fix: first\n",
            "unknown: change",
            "fix(scope with spaces): change",
        ]:
            with self.subTest(title=title), self.assertRaises(ValueError):
                validate_title(title)

    def test_metadata_and_tag(self):
        versions = {"version.txt": "1.2.0", "manifest": "1.2.0", "package": "1.2.0"}
        self.assertEqual(validate_versions(versions, "v1.2.0"), "1.2.0")
        for tag in ["v1.2.1", "1.2.0", "v1.2.0-beta.1", "v1.2.0\n"]:
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_versions(versions, tag)
        for invalid in ["01.2.0", "1.2", "1.2.0-beta.1", "1.2.0\n", "1.2٣.0"]:
            with self.subTest(version=invalid), self.assertRaises(ValueError):
                validate_versions({"version.txt": invalid})

    def test_each_version_can_block_release(self):
        versions = {
            "version.txt": "1.2.0",
            "manifest": "1.2.0",
            "package": "1.2.0",
            "lock root": "1.2.0",
            "uv project": "1.2.0",
            "OpenAPI": "1.2.0",
        }
        for name in versions:
            changed = copy.copy(versions)
            changed[name] = "1.2.1"
            with self.subTest(field=name), self.assertRaises(ValueError):
                validate_versions(changed)

    def test_collect_client_and_api_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "web").mkdir()
            (root / "version.txt").write_text("1.2.0\n", encoding="utf-8")
            (root / ".release-please-manifest.json").write_text('{".": "1.2.0"}', encoding="utf-8")
            package = {"name": "synthetic-app", "version": "1.2.0"}
            lock = {
                "version": "1.2.0",
                "packages": {"": package, "node_modules/example": {"version": "9.0.0"}},
            }
            for name, value in [("package.json", package), ("package-lock.json", lock)]:
                (root / "web" / name).write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(validate_versions(collect_versions(root)), "1.2.0")
            for name in ["package.json", "package-lock.json"]:
                (root / "web" / name).rename(root / name)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "synthetic-api"\nversion = "1.2.0"\n', encoding="utf-8"
            )
            (root / "uv.lock").write_text(
                '[[package]]\nname = "synthetic-api"\nversion = "1.2.0"\n'
                'source = { virtual = "." }\n'
                '[[package]]\nname = "other"\nversion = "9.0.0"\n',
                encoding="utf-8",
            )
            (root / "app").mkdir()
            (root / "app/main.py").write_text('app = FastAPI(version="1.2.0")\n', encoding="utf-8")
            (root / "contracts").mkdir()
            (root / "contracts/openapi.json").write_text(
                '{"info": {"version": "1.2.0"}}', encoding="utf-8"
            )
            self.assertEqual(validate_versions(collect_versions(root)), "1.2.0")
            (root / "contracts/openapi.json").write_text(
                '{"info": {"version": "1.1.0"}}', encoding="utf-8"
            )
            with self.assertRaises(ValueError):
                validate_versions(collect_versions(root))


if __name__ == "__main__":
    unittest.main()
