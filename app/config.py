from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

DEV_SECRET = "development-only-change-before-deploying-000000000000"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MIROVEN_", extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://miroven:miroven_dev@127.0.0.1:5432/miroven"
    )
    secret_key: SecretStr = SecretStr(DEV_SECRET)
    public_origin: str = "http://localhost:8000"
    api_origin: str | None = None
    trusted_proxy_ips: str = ""
    cookie_name: str = "miroven_session"
    session_days: int = 30
    code_ttl_seconds: int = 600
    code_attempt_limit: int = 5
    code_resend_seconds: int = 60
    email_hourly_limit: int = 5
    ip_hourly_limit: int = 30
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_from: str = "Miroven <noreply@localhost>"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    db_pool_size: int = 5
    storage_backend: Literal["local", "s3"] = "local"
    blob_directory: Path = Path("data/blobs")
    blob_max_bytes: int = 4 * 1024 * 1024
    storage_quota_bytes: int = 100 * 1024 * 1024
    blob_grace_seconds: int = 86400
    history_retention_days: int = 90
    snapshot_ttl_seconds: int = 3600
    max_snapshots: int = 5
    max_entries: int = 10000
    pro_blob_max_bytes: int = 16 * 1024 * 1024
    pro_storage_quota_bytes: int = 5 * 1024 * 1024 * 1024
    pro_max_entries: int = 100000
    admin_emails: str = ""
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_configuration(self):
        url = make_url(self.database_url.get_secret_value())
        if url.drivername != "postgresql+psycopg":
            raise ValueError("PostgreSQL with psycopg is required")
        origin = urlsplit(self.public_origin)
        if (
            origin.scheme not in ("http", "https")
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in ("", "/")
            or origin.query
            or origin.fragment
        ):
            raise ValueError("public_origin must be an HTTP(S) origin without a path")
        self.public_origin = self.public_origin.rstrip("/")
        api_origin = urlsplit(self.api_origin or self.public_origin)
        if (
            api_origin.scheme not in ("http", "https")
            or not api_origin.hostname
            or api_origin.username
            or api_origin.password
            or api_origin.path not in ("", "/")
            or api_origin.query
            or api_origin.fragment
        ):
            raise ValueError("api_origin must be an HTTP(S) origin without a path")
        if self.api_origin is not None:
            self.api_origin = self.api_origin.rstrip("/")
        positive = (
            self.session_days,
            self.code_ttl_seconds,
            self.code_attempt_limit,
            self.code_resend_seconds,
            self.email_hourly_limit,
            self.ip_hourly_limit,
            self.db_pool_size,
            self.blob_max_bytes,
            self.storage_quota_bytes,
            self.blob_grace_seconds,
            self.history_retention_days,
            self.snapshot_ttl_seconds,
            self.max_snapshots,
            self.max_entries,
            self.pro_blob_max_bytes,
            self.pro_storage_quota_bytes,
            self.pro_max_entries,
        )
        if min(positive) < 1 or not 1 <= self.smtp_port <= 65535:
            raise ValueError("Limits, durations and ports must be positive")
        if len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("secret_key must contain at least 32 characters")
        if self.blob_max_bytes > 16 * 1024 * 1024 or self.pro_blob_max_bytes > 16 * 1024 * 1024:
            raise ValueError("blob_max_bytes must not exceed 16 MiB")
        for email in self.admin_email_set:
            if "@" not in email or email.startswith("@") or email.endswith("@"):
                raise ValueError("admin_emails must contain valid email addresses")
        if self.storage_backend == "s3" and not self.s3_bucket:
            raise ValueError("S3 storage requires s3_bucket")
        if bool(self.s3_access_key) != bool(self.s3_secret_key):
            raise ValueError("Provide both S3 credentials or use the SDK credential chain")
        if self.environment == "production":
            if self.secret_key.get_secret_value() == DEV_SECRET:
                raise ValueError("Production requires a generated secret_key")
            if origin.scheme != "https":
                raise ValueError("Production requires an HTTPS public_origin")
            if api_origin.scheme != "https":
                raise ValueError("Production requires an HTTPS api_origin")
            if url.password == "miroven_dev":
                raise ValueError("Production requires a non-development database password")
            if self.smtp_username and not self.smtp_starttls:
                raise ValueError("SMTP authentication requires STARTTLS")
        return self

    @property
    def secure_cookies(self) -> bool:
        return (self.api_origin or self.public_origin).startswith("https://")

    @property
    def admin_email_set(self) -> set[str]:
        return {item.strip().lower() for item in self.admin_emails.split(",") if item.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
