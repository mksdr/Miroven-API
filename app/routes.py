import base64
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import check_csrf, principal, request_code, verify_code
from app.db import get_db
from app.errors import ApiError
from app.models import AuthSession, Vault
from app.schemas import (
    CodeAccepted,
    CodeRequest,
    CodeVerify,
    ErrorResponse,
    HealthResponse,
    SessionResponse,
    VaultResponse,
    VaultWrite,
)
from app.security import digest

ERRORS = {
    code: {"model": ErrorResponse}
    for code in (
        400,
        401,
        403,
        404,
        408,
        409,
        410,
        413,
        415,
        422,
        429,
        500,
        503,
    )
}
router = APIRouter(responses=ERRORS)
Database = Annotated[Session, Depends(get_db)]


@router.get("/health/live", response_model=HealthResponse, tags=["health"])
def live():
    return HealthResponse()


@router.get("/health/ready", response_model=HealthResponse, tags=["health"])
def ready(db: Database):
    try:
        db.execute(text("SELECT 1"))
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        if revision != "0005_subscriptions":
            raise ApiError(503, "not_ready", "The service is not ready.")
    except SQLAlchemyError as exc:
        raise ApiError(503, "not_ready", "The service is not ready.") from exc
    return HealthResponse()


@router.post(
    "/api/v1/auth/code/request", response_model=CodeAccepted, status_code=202, tags=["auth"]
)
def code_request(body: CodeRequest, request: Request, db: Database):
    request_code(
        db,
        request.app.state.settings,
        str(body.email).lower(),
        request.client.host if request.client else "unknown",
    )
    db.commit()
    return CodeAccepted()


@router.post("/api/v1/auth/code/verify", response_model=SessionResponse, tags=["auth"])
def code_verify(body: CodeVerify, request: Request, response: Response, db: Database):
    settings = request.app.state.settings
    result = verify_code(
        db,
        settings,
        str(body.email).lower(),
        body.code,
        request.client.host if request.client else "unknown",
    )
    # Incorrect attempts and verification budgets must survive unsuccessful authentication.
    db.commit()
    if result is None:
        raise ApiError(401, "invalid_code", "The sign-in code is invalid or expired.")
    user, token = result
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )
    return SessionResponse(
        user_id=user.id,
        email=user.email,
        csrf_token=digest(settings, "csrf", token),
    )


@router.get("/api/v1/auth/session", response_model=SessionResponse, tags=["auth"])
def session_info(request: Request, db: Database):
    user, _session, token = principal(request, db)
    return SessionResponse(
        user_id=user.id,
        email=user.email,
        csrf_token=digest(request.app.state.settings, "csrf", token),
    )


def logout(request: Request, response: Response, db: Session, all_sessions: bool):
    user, session, token = principal(request, db)
    check_csrf(request, token)
    stmt = update(AuthSession).where(AuthSession.user_id == user.id)
    if not all_sessions:
        stmt = stmt.where(AuthSession.id == session.id)
    db.execute(stmt.values(revoked_at=datetime.now(UTC)))
    db.commit()
    response.delete_cookie(
        request.app.state.settings.cookie_name,
        path="/",
        httponly=True,
        secure=request.app.state.settings.secure_cookies,
        samesite="lax",
    )
    response.status_code = 204
    return response


@router.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
def logout_current(request: Request, response: Response, db: Database):
    return logout(request, response, db, False)


@router.post("/api/v1/auth/logout-all", status_code=204, tags=["auth"])
def logout_all(request: Request, response: Response, db: Database):
    return logout(request, response, db, True)


def vault_response(vault: Vault) -> VaultResponse:
    return VaultResponse(
        version=vault.version,
        wrapped_vault_key=base64.b64encode(vault.wrapped_vault_key).decode(),
        wrapping_nonce=base64.b64encode(vault.wrapping_nonce).decode(),
        recovery_salt=base64.b64encode(vault.recovery_salt).decode(),
    )


@router.get("/api/v1/vault", response_model=VaultResponse, tags=["vault"])
def get_vault(request: Request, db: Database):
    user, _session, _token = principal(request, db)
    vault = db.get(Vault, user.id)
    if vault is None:
        raise ApiError(404, "vault_not_found", "No vault has been registered.")
    return vault_response(vault)


@router.put("/api/v1/vault", response_model=VaultResponse, tags=["vault"])
def put_vault(body: VaultWrite, request: Request, db: Database):
    user, _session, token = principal(request, db)
    check_csrf(request, token)
    # The user lock also protects the first insert, when no vault row exists yet.
    vault = db.get(Vault, user.id)
    version = vault.version if vault else 0
    if body.expected_version != version:
        raise ApiError(409, "vault_conflict", "The vault was changed by another device.")
    if vault is None:
        vault = Vault(user_id=user.id)
        db.add(vault)
    vault.version = version + 1
    vault.wrapped_vault_key = base64.b64decode(body.wrapped_vault_key)
    vault.wrapping_nonce = base64.b64decode(body.wrapping_nonce)
    vault.recovery_salt = base64.b64decode(body.recovery_salt)
    vault.updated_at = datetime.now(UTC)
    db.commit()
    return vault_response(vault)
