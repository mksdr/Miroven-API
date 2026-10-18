"""Create dev/test databases on the disposable host-local PostgreSQL instance (127.0.0.1:55432).

Pairs with scripts/bootstrap_local.py; not needed when running PostgreSQL via Docker Compose.
"""

import sys
from pathlib import Path

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import get_settings  # noqa: E402

url = make_url(get_settings().database_url.get_secret_value())
if url.host != "127.0.0.1" or url.port != 55432:
    raise SystemExit("This helper only targets the disposable local instance on 127.0.0.1:55432")
with psycopg.connect(
    host=url.host,
    port=url.port,
    dbname="postgres",
    user=url.username,
    password=url.password,
    autocommit=True,
) as connection:
    for name in ("miroven", "miroven_test"):
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            print(f"Created {name}")
