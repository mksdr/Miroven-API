import base64
import binascii
import hmac
import json
from uuid import UUID

from app.config import Settings
from app.errors import ApiError
from app.models import SyncState
from app.security import digest


def seal_cursor(
    settings: Settings,
    user_id: UUID,
    state: SyncState,
    position: int,
    snapshot_id: UUID | None = None,
    after: UUID | None = None,
) -> str:
    payload = {"u": str(user_id), "e": str(state.epoch), "p": position, "k": "changes"}
    if snapshot_id is not None:
        payload.update(k="snapshot", s=str(snapshot_id), a=str(after) if after else None)
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return encoded + "." + digest(settings, "sync-cursor", encoded)


def open_cursor(settings: Settings, user_id: UUID, state: SyncState, token: str, kind: str):
    try:
        encoded, signature = token.split(".")
        if not hmac.compare_digest(signature, digest(settings, "sync-cursor", encoded)):
            raise ValueError
        payload = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
        keys = {"u", "e", "p", "k"} | ({"s", "a"} if kind == "snapshot" else set())
        if (
            set(payload) != keys
            or payload["u"] != str(user_id)
            or payload["k"] != kind
            or type(payload["p"]) is not int
            or not 0 <= payload["p"] <= 2**63 - 1
        ):
            raise ValueError
        if kind == "snapshot":
            UUID(payload["s"])
            if payload["a"] is not None:
                UUID(payload["a"])
    except (ValueError, TypeError, KeyError, binascii.Error):
        raise ApiError(400, "invalid_cursor", "The cursor is invalid.") from None
    if payload["e"] != str(state.epoch):
        raise ApiError(410, "cursor_expired", "Create a snapshot to resume synchronization.")
    return payload
