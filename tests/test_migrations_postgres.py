from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

pytestmark = pytest.mark.postgres


def test_initial_migration_roundtrip_and_model_consistency(pg, postgres_settings):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.attributes["settings"] = postgres_settings
    command.downgrade(config, "base")
    assert inspect(pg).get_table_names() == ["alembic_version"]
    command.upgrade(config, "head")
    command.check(config)
