# Generated API contracts

Consult this guide for route/schema changes, including implementation edits in `app/`. Commands run from the API repository root unless stated otherwise.

- `openapi.json` and `api.d.ts` are generated; change API source rather than editing output manually. Maintain `contract-check.ts` when contract assertions change.
- Generate OpenAPI with `uv run python scripts/export_openapi.py`.
- Generate TypeScript with `npm run generate`, then run `npm run typecheck`.
- From `../Miroven/web/`, run `npm run api:generate` and `npm run api:check` to update and verify the client copy.
- Check OpenAPI freshness with `uv run python scripts/export_openapi.py --check`; inspect `git diff -- contracts` for intended changes. CI also checks regeneration leaves no unexpected diff.
- Preserve client compatibility and verify affected callers and integration tests. Types alone do not verify runtime behavior.
- Report missing client synchronization and verification if the client checkout is unavailable.
