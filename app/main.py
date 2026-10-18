from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.account import router as account_router
from app.admin import router as admin_router
from app.config import Settings, get_settings
from app.db import build_engine
from app.errors import (
    ApiError,
    api_error_handler,
    http_error_handler,
    unexpected_error_handler,
    validation_error_handler,
)
from app.middleware import RequestPolicy
from app.routes import router
from app.sync_routes import router as sync_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title="Miroven API",
        version="0.1.0",
        lifespan=lifespan,
        description="Encrypted storage API. Only implemented endpoints are advertised.",
        license_info={"name": "AGPL-3.0-only", "url": "https://www.gnu.org/licenses/agpl-3.0.html"},
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.engine = build_engine(settings)
    app.state.storage = None
    host = urlsplit(settings.api_origin or settings.public_origin).hostname
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[host, "localhost", "127.0.0.1"])
    app.add_middleware(RequestPolicy, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Miroven-User-ID", "Authorization"],
        expose_headers=["X-Request-ID"],
        max_age=600,
    )

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(router)
    app.include_router(sync_router)
    app.include_router(account_router)
    app.include_router(admin_router)
    return app
