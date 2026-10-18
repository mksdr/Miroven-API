from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings


def build_engine(settings: Settings):
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_timeout=5,
        hide_parameters=True,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=10000"},
    )


def get_db(request: Request) -> Generator[Session, None, None]:
    with Session(request.app.state.engine, expire_on_commit=False) as session:
        yield session
