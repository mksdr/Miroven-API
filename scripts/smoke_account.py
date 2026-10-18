"""Local Mailpit-only account lifecycle smoke. Includes a real resend cooldown."""

import argparse
import re
import subprocess
import time
import uuid

import httpx
from smoke_compose import check_sync


def code_for(email, previous=None):
    deadline = time.monotonic() + 30
    with httpx.Client(base_url="http://localhost:8025", timeout=5) as mailpit:
        while time.monotonic() < deadline:
            for message in mailpit.get("/api/v1/messages").json()["messages"]:
                if message["ID"] == previous:
                    continue
                if any(recipient["Address"] == email for recipient in message["To"]):
                    text = mailpit.get("/api/v1/message/" + message["ID"]).json()["Text"]
                    return re.search(r"\b[0-9]{6}\b", text).group(), message["ID"]
            time.sleep(0.5)
    raise AssertionError("No new code arrived")


def main():
    import base64

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restart-worker", action="store_true")
    args = parser.parse_args()
    origin = "http://localhost:8000"
    email = f"p5-smoke-{uuid.uuid4().hex}@example.com"
    with httpx.Client(base_url=origin, headers={"Origin": origin}, timeout=10) as client:
        assert client.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
        code, message_id = code_for(email)
        result = client.post("/api/v1/auth/code/verify", json={"email": email, "code": code})
        assert result.status_code == 200
        client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
        assert (
            client.put(
                "/api/v1/vault",
                json={
                    "expected_version": 0,
                    "wrapped_vault_key": base64.b64encode(bytes(48)).decode(),
                    "wrapping_nonce": base64.b64encode(bytes(12)).decode(),
                    "recovery_salt": base64.b64encode(bytes(16)).decode(),
                },
            ).status_code
            == 200
        )
        check_sync(client, origin)
        usage = client.get("/api/v1/account/usage").json()
        assert usage["storage_bytes"] > 0 and usage["entries"] > 0
        print("Account smoke: ciphertext stored; waiting for reauthentication cooldown", flush=True)
        time.sleep(60)
        assert client.post("/api/v1/auth/code/request", json={"email": email}).status_code == 202
        code, _ = code_for(email, message_id)
        old_cookie = httpx.Cookies(client.cookies)
        if args.restart_worker:
            subprocess.run(["docker", "compose", "stop", "worker"], check=True)
        try:
            response = client.request("DELETE", "/api/v1/account", json={"code": code})
            assert response.status_code == 202
            receipt = response.json()
            if args.restart_worker:
                assert (
                    client.get(
                        "/api/v1/account/deletion",
                        headers={
                            "Authorization": "Bearer " + receipt["deletion_token"],
                        },
                    ).json()["status"]
                    == "pending"
                )
        finally:
            if args.restart_worker:
                subprocess.run(["docker", "compose", "start", "worker"], check=True)
        client.cookies = old_cookie
        assert client.get("/api/v1/auth/session").status_code == 401
        assert client.get("/api/v1/sync/changes").status_code == 401
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = client.get(
                "/api/v1/account/deletion",
                headers={
                    "Authorization": "Bearer " + receipt["deletion_token"],
                },
            )
            assert status.status_code == 200
            if status.json()["status"] == "complete":
                print("Account smoke passed: reauthentication, immediate block, worker purge")
                return
            time.sleep(0.5)
        raise AssertionError("Deletion did not finish")


if __name__ == "__main__":
    main()
