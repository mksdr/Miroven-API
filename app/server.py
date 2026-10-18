import uvicorn

from app.config import get_settings
from app.observability import LOG_CONFIG

if __name__ == "__main__":
    try:
        settings = get_settings()
        uvicorn.run(
            "app.main:create_app",
            factory=True,
            host="0.0.0.0",
            port=8000,
            proxy_headers=bool(settings.trusted_proxy_ips),
            forwarded_allow_ips=settings.trusted_proxy_ips or None,
            access_log=False,
            log_config=LOG_CONFIG,
        )
    except Exception as exc:
        raise SystemExit(f"server_error:{type(exc).__name__}") from None
