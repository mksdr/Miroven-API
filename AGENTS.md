# Miroven-API development guide

## Scope and workflow

- Miroven-API provides email authentication/sessions, encrypted synchronization, and blob storage/cleanup. FastAPI code lives in `app/`.
- The sibling `../Miroven/` is the journal client; its application lives in `web/`. `../Miroven-Web/` is the public website.
- Follow `pyproject.toml`, `package.json`, and lockfiles for runtime/tool requirements. Commands run from this repository root unless stated otherwise.
- Keep changes focused, preserve existing work, and avoid unrelated refactors or dependency updates.
- For cross-cutting changes, identify client impact, compatibility, migrations, and verification first. Record material decisions, remaining work, and observed results in the task plan.
- Follow scoped guides in `contracts/`, `migrations/`, `tests/`, and `deploy/`. Consult their `AGENTS.md` files also when changes elsewhere affect those domains.

## Technical documentation

- **You may use Miroven.wiki as technical documentation.** The local checkout is `../Miroven Wiki/Miroven.wiki/`; the online version is <https://github.com/mksdr/Miroven/wiki>.
- These paths are relative to this repository root. Use the online Wiki if the local checkout is unavailable.
- Read relevant pages only: `API-Development.md` for setup, `API-Authentication.md` and `Security-Model.md` for security, `API-Sync-Protocol.md` for synchronization, and `Architecture.md`/`Cryptographic-Format.md` for client boundaries.
- Update corresponding English and Korean Wiki pages when documented behavior or contracts change. Explain documentation/code discrepancies and assess compatibility before changing either.

## Shared guarantees

- Never accept or decrypt plaintext journal content, photos, PINs, recovery codes, or plaintext vault keys. Preserve the documented metadata visibility boundary.
- Preserve ownership/account isolation, session checks, Origin/CSRF protection, revisions, operation-ID idempotency, opaque cursors, snapshot consistency, and conflict data.
- Do not remove fields or behavior still used by clients without an explicit compatibility strategy.
- Database changes require Alembic migrations and upgrade checks against existing data.
- Deletion, worker, cleanup, and restoration changes must preserve DB/blob consistency, retry safety, and deletion-ledger replay.
- OpenAPI and TypeScript contracts are generated artifacts. Follow `contracts/AGENTS.md` for route/schema changes and client synchronization.

## Verification and data

- Prepare Python dependencies with `uv sync --locked`; prepare contract tools with `npm ci`.
- For Python changes, run `uv run ruff check app scripts tests migrations deploy/truenas/manage.py` and `uv run ruff format --check app scripts tests migrations deploy/truenas/manage.py`.
- Use `uv run pytest -q` with the requirements in `tests/AGENTS.md`. Add meaningful regression coverage; reproduce bugs before fixing them when practical.
- For authentication, synchronization, or account-flow changes, verify client behavior and actual API integration using the Wiki's `Client-Development.md`.
- Report commands, passes, failures, skips, and environment limitations separately. Record both tested commits and relevant uncommitted changes for integration. Local success does not establish remote CI or production success.
- Preserve existing `.env`, credentials, and local data. Do not delete databases/volumes as routine cleanup. Use synthetic data; keep real credentials, email payloads, session/deletion-receipt tokens, and personal records out of logs, artifacts, and commits.
- Follow `CONTRIBUTING.md`, `SECURITY.md`, licenses, and third-party notices.
