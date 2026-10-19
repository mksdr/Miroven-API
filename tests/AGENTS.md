# API tests

Consult this guide when running or modifying API tests. Commands run from the API repository root.

- Run `uv run pytest -q`. Use focused tests during iteration and broaden verification as affected behavior requires.
- Fixtures truncate tables. Set `TEST_DATABASE_URL` only to a dedicated database whose name ends in `_test`. Never use production or ordinary development data; preserve existing safety checks.
- Missing `TEST_DATABASE_URL` causes PostgreSQL tests to skip. Report skips rather than claiming database verification passed.
- Follow the Wiki's `API-Development.md` for Compose versus host PostgreSQL; helper scripts have specific host/port requirements.
- Use real PostgreSQL for affected database behavior and migrations; mocks do not verify these guarantees.
- Test observable behavior, failure paths, isolation, and compatibility. Demonstrate failure before bug fixes when practical.
- Use synthetic accounts and ciphertext. Keep credentials and sensitive payloads out of failure output and fixtures.
- Distinguish local storage/Moto emulation from real external S3 validation. See `API-Validation.md` for verification limits.
