from email import policy
from email.parser import BytesParser

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.email import render
from app.email.policy import DEFAULT_GROUPS, email_locale, signup_allowed
from app.main import create_app
from app.worker import send_email


def test_default_groups_match_supplied_domains_and_language_routes():
    groups = Settings(_env_file=None).email_domain_groups
    assert groups == DEFAULT_GROUPS
    assert [len(groups[key]) for key in ("DEFAULT", "KR", "JP")] == [12, 11, 4]
    for group, locale in (("DEFAULT", "en"), ("KR", "ko"), ("JP", "ja")):
        for domain in groups[group]:
            assert signup_allowed(groups, f"synthetic@{domain.upper()}")
            assert email_locale(groups, f"synthetic@{domain.upper()}") == locale
    assert not signup_allowed(groups, "synthetic@example.com")
    assert email_locale(groups, "synthetic@example.com") == "en"


@pytest.mark.parametrize(
    "domain", ["fakegmail.com", "gmail.com.evil.test", "sub.gmail.com", "naver.com.evil.test"]
)
def test_similar_and_subdomains_are_not_allowed(domain):
    assert not signup_allowed(DEFAULT_GROUPS, f"synthetic@{domain}")
    assert email_locale(DEFAULT_GROUPS, f"synthetic@{domain}") == "en"


@pytest.mark.parametrize(
    "email",
    [
        "synthetic+tag@gmail.com",
        "synthetic+@naver.com",
        "synthetic.name@gmail.com",
        "synthetic.name+tag@yahoo.co.jp",
    ],
)
def test_signup_refuses_plus_and_dot_in_local_part(email):
    assert not signup_allowed(DEFAULT_GROUPS, email)


def test_domain_dots_are_allowed_and_addresses_are_not_rewritten():
    assert signup_allowed(DEFAULT_GROUPS, "synthetic@privaterelay.appleid.com")
    assert not signup_allowed(DEFAULT_GROUPS, "gmail.com")


def test_configuration_normalizes_groups_and_allows_explicit_empty_groups():
    settings = Settings(
        _env_file=None,
        email_domains_default=" Example.com, CUSTOM.COM ",
        email_domains_kr="",
        email_domains_jp="",
    )
    assert settings.email_domain_groups == {
        "DEFAULT": ["example.com", "custom.com"],
        "KR": [],
        "JP": [],
    }


@pytest.mark.parametrize(
    "domain",
    [
        "@gmail.com",
        "*.gmail.com",
        "gmail.com,",
        "gmail..com",
        "https://gmail.com",
        "localhost",
        "-gmail.com",
        "gmail-.com",
        "gmail.com.",
        "gmail.com,gmail.com",
        "지메일.com",
    ],
)
def test_configuration_refuses_invalid_or_duplicate_domains(domain):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, email_domains_default=domain)


def test_configuration_refuses_overlapping_or_entirely_empty_groups():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, email_domains_default="naver.com")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, email_domains_default="", email_domains_kr="", email_domains_jp="")


def test_public_policy_is_account_independent_and_not_cached():
    settings = Settings(_env_file=None)
    with TestClient(create_app(settings), base_url="http://localhost:8000") as client:
        response = client.get("/api/v1/auth/policy")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "domain_groups": settings.email_domain_groups,
        "default_email_locale": "en",
        "existing_accounts_allowed": True,
        "blocked_local_part_characters": ["+", "."],
    }


def test_shipped_catalogs_are_complete_and_have_valid_placeholders():
    for locale in render.SUPPORTED_LOCALES:
        render.validate_catalog(render.load_catalog(locale), complete=True)


@pytest.mark.parametrize(
    "catalog",
    [
        {"unknown": "Text"},
        {"code": "Code {otp}"},
        {"code": "Code {code!r}"},
        {"code": "Code {code.value}"},
        {"code": "Code {code:06}"},
        {"subject": ""},
        {"subject": "Header\nInjection"},
        {"safety": None},
    ],
)
def test_invalid_translation_fails_validation(catalog):
    with pytest.raises(ValueError):
        render.validate_catalog(catalog)


def test_missing_translation_falls_back_per_message(monkeypatch):
    english = render.load_catalog("en")
    monkeypatch.setattr(
        render, "load_catalog", lambda locale: {"subject": "인증"} if locale == "ko" else english
    )
    subject, body = render.render_verification_email({"locale": "ko", "code": "012345"})
    assert subject == "인증"
    assert "Your Miroven verification code is 012345." in body
    assert english["vault"] in body


@pytest.mark.parametrize("locale", [None, "fr", "../../private", ["ko"]])
def test_unknown_locales_use_english(locale):
    subject, body = render.render_verification_email({"locale": locale, "code": "012345"})
    assert subject == render.load_catalog("en")["subject"]
    assert "012345" in body


@pytest.mark.parametrize(
    ("locale", "subject", "phrase"),
    [
        ("en", "Your Miroven verification code", "cannot unlock an encrypted vault"),
        ("ko", "Miroven 인증 코드", "암호화된 금고를 해제할 수 없습니다"),
        ("ja", "Miroven 認証コード", "暗号化された保管庫のロックは解除できません"),
    ],
)
def test_smtp_roundtrip_encodes_localized_subject_body_and_actual_expiry(
    monkeypatch, locale, subject, phrase
):
    messages = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def send_message(self, message):
            messages.append(message.as_bytes())

    monkeypatch.setattr("app.worker.smtplib.SMTP", SMTP)
    send_email(
        Settings(_env_file=None, smtp_starttls=False, smtp_username=None),
        {
            "email": "synthetic@example.com",
            "code": "012345",
            "locale": locale,
            "expires_at": "2030-01-02T12:30:45+09:00",
        },
    )
    message = BytesParser(policy=policy.default).parsebytes(messages[0])
    assert str(message["Subject"]) == subject
    assert message.get_content_charset() == "utf-8"
    body = message.get_content()
    assert "012345" in body and phrase in body
    assert "2030-01-02 03:30:45" in body and "UTC" in body


def test_legacy_payload_and_invalid_expiry_remain_deliverable_in_english():
    for fields in ({}, {"expires_at": "broken"}, {"expires_at": "2030-01-02T03:30:45"}):
        subject, body = render.render_verification_email({"code": "012345", **fields})
        assert subject == render.load_catalog("en")["subject"]
        assert render.load_catalog("en")["expires_soon"] in body
