"""Export only the implemented API contract, without connecting to a database."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
app = create_app(Settings(_env_file=None, environment="test"))
target = Path(__file__).resolve().parents[1] / "contracts" / "openapi.json"
content = json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
app.state.engine.dispose()
if args.check:
    if not target.exists() or target.read_text(encoding="utf-8") != content:
        raise SystemExit("OpenAPI contract is stale; run python scripts/export_openapi.py")
else:
    target.parent.mkdir(exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    print("Exported contracts/openapi.json")
