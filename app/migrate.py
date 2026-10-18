"""Production migration entry point without credential-bearing exception output."""

import logging.config

from alembic import command
from alembic.config import Config

from app.observability import LOG_CONFIG

if __name__ == "__main__":
    logging.config.dictConfig(LOG_CONFIG)
    try:
        command.upgrade(Config("alembic.ini"), "head")
    except Exception as exc:
        raise SystemExit(f"migration_error:{type(exc).__name__}") from None
