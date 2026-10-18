import base64
import hashlib
import hmac
import json
import secrets

from cryptography.fernet import Fernet

from app.config import Settings


def digest(settings: Settings, purpose: str, value: str) -> str:
    return hmac.new(
        settings.secret_key.get_secret_value().encode(),
        (purpose + "\0" + value).encode(),
        hashlib.sha256,
    ).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def payload_cipher(settings: Settings) -> Fernet:
    key = bytes.fromhex(digest(settings, "job-encryption", "v1"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_job(settings: Settings, payload: dict) -> str:
    return payload_cipher(settings).encrypt(json.dumps(payload).encode()).decode()


def decrypt_job(settings: Settings, payload: str) -> dict:
    return json.loads(payload_cipher(settings).decrypt(payload.encode()))
