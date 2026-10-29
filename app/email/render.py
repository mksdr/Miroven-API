import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from string import Formatter

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "ko", "ja")
CATALOG_DIRECTORY = Path(__file__).with_name("locales")
MESSAGE_FIELDS = {
    "subject": set(),
    "code": {"code"},
    "expires_at": {"expires_at"},
    "expires_soon": set(),
    "safety": set(),
    "vault": set(),
}


def validate_catalog(catalog: dict[str, str], *, complete: bool = False) -> None:
    if not isinstance(catalog, dict) or catalog.keys() - MESSAGE_FIELDS.keys():
        raise ValueError("Email catalog contains unknown message keys")
    if complete and catalog.keys() != MESSAGE_FIELDS.keys():
        raise ValueError("Email catalog is missing message keys")
    for key, value in catalog.items():
        if (
            not isinstance(value, str)
            or not value.strip()
            or (key == "subject" and ("\n" in value or "\r" in value))
        ):
            raise ValueError("Email messages must be nonempty strings with a single-line subject")
        fields = set()
        for _, field, spec, conversion in Formatter().parse(value):
            if field is not None:
                if spec or conversion:
                    raise ValueError("Email placeholders cannot use format specs or conversions")
                fields.add(field)
        if fields != MESSAGE_FIELDS[key]:
            raise ValueError("Email translation placeholders must match the English message")


@cache
def load_catalog(locale: str) -> dict[str, str]:
    # Never use an untrusted payload value as a filesystem path.
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    catalog = json.loads((CATALOG_DIRECTORY / f"{locale}.json").read_text(encoding="utf-8"))
    validate_catalog(catalog, complete=locale == DEFAULT_LOCALE)
    return catalog


def validate_catalogs() -> None:
    for locale in SUPPORTED_LOCALES:
        load_catalog(locale)


def render_verification_email(payload: dict) -> tuple[str, str]:
    locale = payload.get("locale", DEFAULT_LOCALE)
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    copy = {**load_catalog(DEFAULT_LOCALE), **load_catalog(locale)}
    expiry_key = "expires_soon"
    expires_at = ""
    try:
        expiry = datetime.fromisoformat(payload["expires_at"])
        if expiry.tzinfo is not None:
            expires_at = expiry.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
            expiry_key = "expires_at"
    except (KeyError, TypeError, ValueError):
        pass
    body = "\n".join(
        [
            copy["code"].format(code=payload["code"]),
            copy[expiry_key].format(expires_at=expires_at),
            copy["safety"],
            copy["vault"],
        ]
    )
    return copy["subject"], body + "\n"
