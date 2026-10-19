# Deployment configuration and tooling

Consult this guide for deployment, Compose, container, worker-operation, or recovery changes, including related files outside this directory.

- Use the Wiki's `API-Deployment.md` and `API-Operations.md` for deployment, backups, deletion-ledger replay, and recovery.
- Keep development Compose/Mailpit assumptions separate from production configuration.
- Preserve credentials, environment files, databases, and volumes. Do not delete volumes as routine cleanup.
- Preserve DB/blob backup consistency and replay the deletion ledger before exposing restored data.
- Follow relevant Compose smoke checks and container verification in `.github/workflows/ci.yml`. Include `deploy/truenas/manage.py` in Python lint/format checks when affected.
- Verify changed worker retries, deletion, and storage cleanup with relevant tests.
- Report local coverage accurately. DNS/TLS, external SMTP, real storage providers, and TrueNAS deployment require their own evidence.
