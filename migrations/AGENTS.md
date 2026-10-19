# Database migrations

Consult this guide for model/schema changes and edits in this directory. Commands run from the API repository root.

- Use the Wiki's `API-Development.md` for setup and `API-Operations.md` for recovery constraints.
- Preserve the revision chain. Add Alembic revisions rather than rewriting previously released migrations.
- Check model/schema agreement, upgrades with existing records, and preservation of ownership, revision, deletion, and blob relationships.
- Verify relevant `tests/test_migrations_postgres.py` coverage against dedicated PostgreSQL, following `tests/AGENTS.md`.
- Creating fresh tables from model metadata does not verify an existing database upgrade.
- Describe recovery/rollback limitations for destructive or irreversible migrations. Do not reset an existing database to make an upgrade pass.
