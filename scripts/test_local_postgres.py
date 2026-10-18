"""Run pytest against `miroven_test` on the host-local PostgreSQL instance (127.0.0.1:55432).

For Compose or custom PostgreSQL instances, set TEST_DATABASE_URL=<...>_test and run pytest.
"""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402

url = make_url(get_settings().database_url.get_secret_value())
if url.host != "127.0.0.1" or url.port != 55432:
    raise SystemExit("Use TEST_DATABASE_URL explicitly for other database instances")
env = os.environ.copy()
env["TEST_DATABASE_URL"] = url.set(database="miroven_test").render_as_string(hide_password=False)
raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", *sys.argv[1:]], env=env))
