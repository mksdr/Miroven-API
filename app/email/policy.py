import json
import re
from pathlib import Path

GROUP_LOCALES = {"DEFAULT": "en", "KR": "ko", "JP": "ja"}
BLOCKED_LOCAL_PART_CHARACTERS = ("+", ".")
DEFAULT_GROUPS = json.loads(Path(__file__).with_name("domains.json").read_text(encoding="utf-8"))
DOMAIN_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def normalize_domains(value: str) -> str:
    if not value.strip():
        return ""
    domains = [item.strip().lower() for item in value.split(",")]
    for domain in domains:
        labels = domain.split(".")
        if (
            len(domain) > 253
            or len(labels) < 2
            or any(DOMAIN_PATTERN.fullmatch(label) is None for label in labels)
        ):
            raise ValueError("Email domain groups require exact ASCII DNS domains")
    if len(set(domains)) != len(domains):
        raise ValueError("Email domain groups must not contain duplicate domains")
    return ",".join(domains)


def email_locale(groups: dict[str, list[str]], email: str) -> str:
    domain = email.rsplit("@", 1)[-1].lower()
    for group, locale in GROUP_LOCALES.items():
        if domain in groups[group]:
            return locale
    return "en"


def signup_allowed(groups: dict[str, list[str]], email: str) -> bool:
    local_part, separator, domain = email.rpartition("@")
    return (
        bool(separator and local_part)
        and not any(character in local_part for character in BLOCKED_LOCAL_PART_CHARACTERS)
        and any(domain.lower() in domains for domains in groups.values())
    )
