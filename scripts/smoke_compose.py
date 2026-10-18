"""Verify the running development Compose stack without printing codes or session tokens."""

import argparse
import base64
import json
import os
import re
import time
import uuid

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def encrypt(vault_key, kind, object_id, revision, plaintext):
    data_key = AESGCM.generate_key(bit_length=256)
    nonce, key_nonce = os.urandom(12), os.urandom(12)
    content_aad = f"private-journal:sync:{kind}:{object_id}:r{revision}:v1".encode()
    key_aad = f"private-journal:sync:{kind}:data-key:{object_id}:v1".encode()

    def encode(value):
        return base64.b64encode(value).decode()

    return {
        "ciphertext": encode(AESGCM(data_key).encrypt(nonce, plaintext, content_aad)),
        "content_nonce": encode(nonce),
        "key_nonce": encode(key_nonce),
        "wrapped_data_key": encode(AESGCM(vault_key).encrypt(key_nonce, data_key, key_aad)),
        "crypto_version": 1,
    }


def decrypt(vault_key, kind, object_id, revision, envelope):
    decode = base64.b64decode
    data_key = AESGCM(vault_key).decrypt(
        decode(envelope["key_nonce"]),
        decode(envelope["wrapped_data_key"]),
        f"private-journal:sync:{kind}:data-key:{object_id}:v1".encode(),
    )
    return AESGCM(data_key).decrypt(
        decode(envelope["content_nonce"]),
        decode(envelope["ciphertext"]),
        f"private-journal:sync:{kind}:{object_id}:r{revision}:v1".encode(),
    )


def check_sync(client, origin):
    vault_key = AESGCM.generate_key(bit_length=256)
    entry_id, blob_id = str(uuid.uuid4()), str(uuid.uuid4())
    photo = b"synthetic photo bytes" * 10000
    blob = encrypt(vault_key, "blob", blob_id, 1, photo)
    # Preserve the existing client's on-disk blob envelope keys and AAD unchanged.
    serialized = json.dumps(
        {
            "version": 1,
            "ciphertext": blob["ciphertext"],
            "contentNonce": blob["content_nonce"],
            "wrappedDataKey": blob["wrapped_data_key"],
            "keyNonce": blob["key_nonce"],
        }
    ).encode()
    response = client.post(
        "/api/v1/blobs",
        params={
            "blob_id": blob_id,
            "entry_id": entry_id,
            "revision": 1,
        },
        content=serialized,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200, "Encrypted file upload failed"
    plaintext = json.dumps(
        {"id": entry_id, "text": "Synthetic encrypted journal", "blobId": blob_id}
    ).encode()
    operation = {
        "operation_id": str(uuid.uuid4()),
        "entry_id": entry_id,
        "base_revision": 0,
        "revision": 1,
        "change_type": "upsert",
        "blob_id": blob_id,
        "payload": encrypt(vault_key, "entry", entry_id, 1, plaintext),
    }
    accepted = client.post("/api/v1/sync/push", json=operation)
    assert accepted.status_code == 200
    assert client.post("/api/v1/sync/push", json=operation).json() == accepted.json()
    with httpx.Client(
        base_url=origin,
        cookies=client.cookies,
        headers={
            "Origin": origin,
            "X-CSRF-Token": client.headers["X-CSRF-Token"],
        },
        timeout=10,
    ) as device:
        page = device.get("/api/v1/sync/changes", params={"limit": 1}).json()
        assert decrypt(vault_key, "entry", entry_id, 1, page["items"][0]["payload"]) == plaintext
        downloaded = device.get(f"/api/v1/blobs/{blob_id}")
        assert downloaded.status_code == 200
        assert downloaded.content == serialized
        received = downloaded.json()
        assert (
            decrypt(
                vault_key,
                "blob",
                blob_id,
                1,
                {
                    "ciphertext": received["ciphertext"],
                    "content_nonce": received["contentNonce"],
                    "wrapped_data_key": received["wrappedDataKey"],
                    "key_nonce": received["keyNonce"],
                },
            )
            == photo
        )
        updated = {
            **operation,
            "operation_id": str(uuid.uuid4()),
            "base_revision": 1,
            "revision": 2,
            "blob_id": None,
            "payload": encrypt(vault_key, "entry", entry_id, 2, b"updated"),
        }
        assert device.post("/api/v1/sync/push", json=updated).status_code == 200
    stale = {**updated, "operation_id": str(uuid.uuid4())}
    assert client.post("/api/v1/sync/push", json=stale).status_code == 409
    snapshot = client.get("/api/v1/sync/snapshot").json()
    assert snapshot["items"][0]["revision"] == 2
    assert (
        client.post(
            "/api/v1/sync/push",
            json={
                "operation_id": str(uuid.uuid4()),
                "entry_id": entry_id,
                "base_revision": 2,
                "revision": 3,
                "change_type": "delete",
            },
        ).status_code
        == 200
    )
    delta = client.get("/api/v1/sync/changes", params={"cursor": snapshot["resume_cursor"]}).json()
    assert len(delta["items"]) == 1 and delta["items"][0]["change_type"] == "delete"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--mailpit", default="http://localhost:8025")
    args = parser.parse_args()
    email = f"compose-smoke-{uuid.uuid4().hex}@example.com"
    with httpx.Client(base_url=args.api, headers={"Origin": args.api}, timeout=10) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
        with httpx.Client(base_url=args.mailpit, timeout=5) as mailpit:
            deadline = time.monotonic() + 30
            code = None
            while time.monotonic() < deadline:
                response = mailpit.get("/api/v1/messages")
                response.raise_for_status()
                for message in response.json()["messages"]:
                    if any(recipient["Address"] == email for recipient in message["To"]):
                        detail = mailpit.get("/api/v1/message/" + message["ID"])
                        detail.raise_for_status()
                        match = re.search(r"\b[0-9]{6}\b", detail.json()["Text"])
                        assert match is not None, "The sign-in message contains no code"
                        code = match.group()
                        break
                if code:
                    break
                time.sleep(0.5)
        assert code is not None, "No sign-in email arrived before the deadline"
        response = client.post("/api/v1/auth/code/verify", json={"email": email, "code": code})
        assert response.status_code == 200, "Email code verification failed"
        identity = response.json()
        client.headers["X-CSRF-Token"] = identity["csrf_token"]
        assert client.get("/api/v1/auth/session").json()["user_id"] == identity["user_id"]
        body = {
            "expected_version": 0,
            "wrapped_vault_key": base64.b64encode(bytes(range(48))).decode(),
            "wrapping_nonce": base64.b64encode(bytes(12)).decode(),
            "recovery_salt": base64.b64encode(bytes(16)).decode(),
        }
        assert client.put("/api/v1/vault", json=body).status_code == 200
        assert client.get("/api/v1/vault").json()["version"] == 1
        assert client.put("/api/v1/vault", json=body).status_code == 409
        check_sync(client, args.api)
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/session").status_code == 401
    print(
        "Compose smoke passed: mail, login, encrypted entry/photo, retry, "
        "conflict, snapshot, delete, logout"
    )


if __name__ == "__main__":
    main()
