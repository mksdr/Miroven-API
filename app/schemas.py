import base64
import binascii
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorResponse(Contract):
    code: str
    message: str
    request_id: str


class HealthResponse(Contract):
    status: Literal["ok"] = "ok"


class CodeRequest(Contract):
    email: Annotated[EmailStr, Field(max_length=320)]


class CodeVerify(CodeRequest):
    code: str = Field(pattern=r"^[0-9]{6}$", min_length=6, max_length=6)


class CodeAccepted(Contract):
    message: str = "If delivery is available, a sign-in code will arrive shortly."


class SessionResponse(Contract):
    user_id: UUID
    email: EmailStr
    csrf_token: str


class VaultWrite(Contract):
    expected_version: int = Field(ge=0, le=2147483646)
    wrapped_vault_key: str = Field(min_length=64, max_length=64)
    wrapping_nonce: str = Field(min_length=16, max_length=16)
    recovery_salt: str = Field(min_length=24, max_length=24)
    recovery_format: Literal["base32-hkdf-sha256-v1"] = "base32-hkdf-sha256-v1"
    crypto_version: Literal[1] = 1
    key_version: Literal[1] = 1

    @field_validator("wrapped_vault_key", "wrapping_nonce", "recovery_salt")
    @classmethod
    def validate_base64(cls, value: str, info):
        lengths = {"wrapped_vault_key": 48, "wrapping_nonce": 12, "recovery_salt": 16}
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Invalid base64") from exc
        if len(decoded) != lengths[info.field_name] or base64.b64encode(decoded).decode() != value:
            raise ValueError("Non-canonical base64 or incorrect byte length")
        return value


class VaultResponse(Contract):
    version: int
    wrapped_vault_key: str
    wrapping_nonce: str
    recovery_salt: str
    recovery_format: Literal["base32-hkdf-sha256-v1"] = "base32-hkdf-sha256-v1"
    crypto_version: Literal[1] = 1
    key_version: Literal[1] = 1
