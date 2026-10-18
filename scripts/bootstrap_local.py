"""Create ignored development settings for a host-local PostgreSQL instance on 127.0.0.1:55432.

For standard Docker Compose development (port 5432), copy .env.example to .env instead.
Never prints or overwrites existing credentials.
"""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env_file = root / ".env"
password_file = root / ".runtime" / "pg-password"
if env_file.exists() or password_file.exists():
    raise SystemExit("Existing development settings preserved; bootstrap skipped.")
password = secrets.token_hex(24)
secret = secrets.token_hex(32)
password_file.parent.mkdir(exist_ok=True)
password_file.write_text(password, encoding="utf-8")
env_file.write_text(
    "MIROVEN_ENVIRONMENT=development\n"
    f"MIROVEN_DATABASE_URL=postgresql+psycopg://miroven:{password}@127.0.0.1:55432/miroven\n"
    f"MIROVEN_SECRET_KEY={secret}\n"
    "MIROVEN_PUBLIC_ORIGIN=http://localhost:8000\n",
    encoding="utf-8",
)
print("Created ignored local development settings (.env and .runtime/pg-password).")
