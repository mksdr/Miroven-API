# Miroven API

**English** · [한국어](README.ko.md)

The optional authentication, encrypted journal/profile sync, and file storage server for Miroven. Runs on Python 3.12, PostgreSQL, and SMTP, with a background worker for email and cleanup. Journal encryption and vault recovery happen in the client; the API stores ciphertext and operational metadata.

## Get started

Requires Docker Compose with a Linux engine. From this repository's root, copy the example only if `.env` does not exist:

```sh
cp .env.example .env
docker compose up -d --build --wait
```

API: `http://localhost:8000`; interactive API reference: `/docs`; development Mailpit: `http://localhost:8025`. Migrations run before the API and worker start. Stop with `docker compose down` to preserve volumes. This default stack is for development; follow the production deployment guide for hosting.

## Documentation

| Goal | Reference |
| --- | --- |
| Develop and test | [API development](https://github.com/mksdr/Miroven/wiki/API-Development) |
| Self-host and configure the client | [API deployment](https://github.com/mksdr/Miroven/wiki/API-Deployment) |
| Delete, back up, restore, and upgrade | [API operations](https://github.com/mksdr/Miroven/wiki/API-Operations) |
| Authentication and security boundaries | [API authentication](https://github.com/mksdr/Miroven/wiki/API-Authentication) |
| Sync, profiles, cursors, files, and S3 | [Sync protocol](https://github.com/mksdr/Miroven/wiki/API-Sync-Protocol) |
| Validation boundaries and launch checks | [Validation](https://github.com/mksdr/Miroven/wiki/API-Validation) |
| Exact request/response types | [OpenAPI](contracts/openapi.json), [TypeScript](contracts/api.d.ts) |

Technical documentation is centralized in the [mksdr/Miroven Wiki](https://github.com/mksdr/Miroven/wiki). The client, API, and website have independent package versions. Hosted-service availability is announced separately.

## Contribute and report issues

Read [CONTRIBUTING.md](CONTRIBUTING.md) for ordinary bugs and contributions. Report vulnerabilities privately through [SECURITY.md](SECURITY.md). Never include journal content, recovery codes, credentials, or session tokens in reports.

## License

[GNU AGPL-3.0-only](LICENSE). Dependencies retain their own licenses.
