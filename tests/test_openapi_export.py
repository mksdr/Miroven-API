"""Exercise the exporter CLI without touching the checked-in contract or a database."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.export_openapi import normalize_numbers

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def exported_contract(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/export_openapi.py", tmp_path / "scripts/export_openapi.py")
    (tmp_path / "app").symlink_to(ROOT / "app", target_is_directory=True)
    script = tmp_path / "scripts/export_openapi.py"
    subprocess.run([sys.executable, str(script)], check=True, capture_output=True, text=True)
    return script, tmp_path / "contracts/openapi.json"


def test_integer_bounds_survive_release_json_serialization(exported_contract):
    script, contract = exported_contract
    content = contract.read_text(encoding="utf-8")
    schema = json.loads(content)
    bound = schema["components"]["schemas"]["SubscriptionUpdate"]["properties"]
    assert type(bound["max_entries_override"]["anyOf"][0]["maximum"]) is int
    assert type(bound["storage_quota_override_bytes"]["anyOf"][0]["minimum"]) is int
    result = subprocess.run(
        [sys.executable, str(script), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("change", ["version", "bound", "format", "missing"])
def test_check_rejects_contract_drift(exported_contract, change):
    script, contract = exported_contract
    content = contract.read_text(encoding="utf-8")
    schema = json.loads(content)
    if change == "missing":
        contract.unlink()
    elif change == "format":
        contract.write_text(json.dumps(schema), encoding="utf-8")
    else:
        if change == "version":
            schema["info"]["version"] = "9.9.9"
        else:
            bounds = schema["components"]["schemas"]["SubscriptionUpdate"]["properties"]
            bounds["max_entries_override"]["anyOf"][0]["maximum"] += 1
        contract.write_text(
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [sys.executable, str(script), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "OpenAPI contract is stale" in result.stderr


def test_normalization_preserves_fractional_values_and_non_numbers():
    schema = {
        "anyOf": [{"minimum": 0.0, "maximum": 10000000.0, "multipleOf": 0.25}],
        "examples": ["1.0", True, False, None, 3, -2.0, -0.0],
    }
    content = json.dumps(normalize_numbers(schema))
    assert json.loads(content) == schema
    assert json.loads(content)["anyOf"][0] == {
        "minimum": 0,
        "maximum": 10000000,
        "multipleOf": 0.25,
    }
    assert json.loads(content)["examples"] == ["1.0", True, False, None, 3, -2, 0]
    assert schema["anyOf"][0]["maximum"] == 10000000.0
    assert type(schema["anyOf"][0]["maximum"]) is float
