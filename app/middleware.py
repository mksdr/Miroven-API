import logging
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings
from app.errors import ApiError


class RequestPolicy:
    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_headers(message: Message):
            if message["type"] == "http.response.start":
                route = scope.get("route")
                logging.getLogger("miroven.requests").info(
                    {
                        "request_id": request_id,
                        "method": scope["method"]
                        if scope["method"]
                        in {"GET", "HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"}
                        else "OTHER",
                        "route": route.path if route else "unmatched",
                        "status": message["status"],
                    }
                )
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        async def reject(status: int, code: str, message: str):
            response = JSONResponse(
                status_code=status,
                content={"code": code, "message": message, "request_id": request_id},
            )
            await response(scope, receive, send_headers)

        headers = {k.lower(): v for k, v in scope["headers"]}
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            origin = headers.get(b"origin", b"").decode("latin-1")
            allowed_origins = {self.settings.public_origin}
            if self.settings.api_origin and (
                scope["path"].startswith("/api/v1/admin")
                or scope["path"].startswith("/api/v1/auth/")
            ):
                allowed_origins.add(self.settings.api_origin)
            if origin not in allowed_origins:
                await reject(403, "origin_rejected", "The request origin is not allowed.")
                return
        if scope["method"] == "POST" and scope["path"] == "/api/v1/blobs":
            size = 0

            async def bounded_receive():
                nonlocal size
                message = await receive()
                size += len(message.get("body", b""))
                max_bytes = scope.get("state", {}).get(
                    "blob_max_bytes", self.settings.blob_max_bytes
                )
                if size > max_bytes:
                    raise ApiError(413, "request_too_large", "The encrypted file is too large.")
                return message

            await self.app(scope, bounded_receive, send_headers)
            return
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            limit = 131072 if scope["path"] == "/api/v1/sync/push" else 65536
            if size > limit:
                await reject(413, "request_too_large", "The request is too large.")
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay_body():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay_body, send_headers)
