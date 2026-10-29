# Contributing

[English](#english) · [한국어](#한국어)

## English

Use the project's [issue tracker](https://github.com/mksdr/Miroven/issues) for ordinary bugs and proposals, identifying **Miroven-API** as the affected component. Include the API version/commit, deployment mode, expected/actual behavior, and synthetic reproduction steps. Discuss substantial changes before implementing them. Report vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue.

Follow [API development](https://github.com/mksdr/Miroven/wiki/API-Development) for setup and checks. Database tests clear tables: use only a dedicated database whose name ends in `_test`. Never run them against production or your ordinary development data.

Keep pull requests focused, explain behavior and compatibility implications, and report checks run or unavailable. Changes to routes or schemas must update generated OpenAPI/TypeScript contracts and the client's copied contract. Preserve revision, idempotency, cursor, ownership, deletion, and backup guarantees. Add relevant regression tests and migrations when needed, and keep the English/Korean Wiki pages aligned.

Never include `.env`, live credentials, SMTP payloads, session tokens, recovery codes, or personal records in reports or commits. Preserve copyright and third-party notices; contributions follow [AGPL-3.0-only](LICENSE).

### Commit conventions

Use Conventional Commits for PR titles and final commits on `main`:

```text
type(optional-scope): summary
type(optional-scope)!: breaking summary
```

Allowed types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `revert`.
Use lowercase types; scope is optional and uses lowercase letters/digits, dots, underscores, hyphens or slashes. English summaries and about 72 characters are recommended; Korean text is accepted and length is not a gate. One purpose per PR. Work-in-progress commits inside a PR are unrestricted; squash merge using the PR title. Direct commits to `main` follow the same format; branch protection must enforce PR use if direct pushes should be blocked. Existing history/tags are preserved. Format reverts as `revert: ...`.

Examples: `fix(sync): preserve pending uploads`, `feat(auth): add signup policy guidance`, `chore(deps-dev): update test tools`. Scopes are suggestions, not a fixed allowlist.

Explain incompatible changes and migration/compatibility checks in the PR and commit body; include `BREAKING CHANGE: ...` when needed so the squash message retains the explanation. A marker never replaces migrations, contract checks, or data compatibility tests. Never include secrets or personal data.

Versions, CHANGELOG, tags and GitHub Releases are managed through Release Please. Follow [the release guide](.github/RELEASING.md#english) for version policy, first-run baselines, activation and recovery. Do not manually bump versions for ordinary feature/fix PRs.

## 한국어

일반 버그와 제안은 프로젝트 [이슈](https://github.com/mksdr/Miroven/issues)에 **Miroven-API** 문제임을 표시하고 작성하세요. API 버전/커밋, 배포 방식, 예상·실제 동작, 합성 데이터 재현 순서를 포함하세요. 큰 변경은 구현 전에 논의하고, 취약점은 공개 이슈 대신 [SECURITY.md](SECURITY.md#한국어)로 신고하세요.

설치·검증은 [API 개발 안내](https://github.com/mksdr/Miroven/wiki/API-Development.ko)를 따릅니다. DB 테스트는 테이블을 비우므로 이름이 `_test`로 끝나는 전용 DB만 사용하세요. 운영 DB나 일반 개발 데이터를 대상으로 실행하면 안 됩니다.

PR은 한 목적에 집중하고 동작·호환성 영향과 실행한 검사, 실행하지 못한 검사를 적어 주세요. 경로·스키마 변경 시 생성 OpenAPI/TypeScript 계약과 클라이언트 복사본을 갱신합니다. revision·멱등성·커서·소유권·삭제·백업 보장을 보존하고 필요한 회귀 테스트와 마이그레이션을 추가하세요. Wiki의 영어·한국어 문서도 함께 갱신합니다.

`.env`·실제 인증정보·메일 payload·세션 토큰·복구 코드·개인 기록을 제보나 커밋에 포함하지 마세요. 저작권과 타사 고지를 유지하며 기여에는 [AGPL-3.0-only](LICENSE)가 적용됩니다.

### 커밋 규칙

PR 제목과 `main`에 남는 최종 커밋은 `type(선택적-scope): 요약` 형식을 사용합니다. 호환성 파괴는 `type(scope)!: 요약`으로 표시합니다.

허용 type은 `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `revert`입니다. type은 소문자로 쓰고 scope는 선택 사항이며 소문자·숫자·점·밑줄·하이픈·슬래시를 허용합니다. 영어 제목과 약 72자를 권장하되 한국어를 허용하고 길이를 강제하지 않습니다. PR은 한 목적에 집중하고 PR 제목으로 squash 병합합니다. 작업 중 커밋은 자유롭게 작성할 수 있습니다. 직접 커밋도 같은 규칙을 따르며 직접 push 차단은 브랜치 보호에서 설정합니다. 기존 이력·태그는 보존하고 되돌리기는 `revert: ...`로 작성합니다.

예: `fix(sync): preserve pending uploads`, `feat(auth): add signup policy guidance`, `chore(deps-dev): update test tools`. scope 예시는 고정 허용 목록이 아닙니다.

호환성을 깨는 변경에는 영향·전환 방법·검증을 설명하고 필요한 경우 본문에 `BREAKING CHANGE: ...`를 남깁니다. squash 메시지에도 설명을 보존합니다. 이 표시는 마이그레이션·계약·데이터 호환성 검증을 대신하지 않습니다. 비밀정보와 개인 데이터는 포함하지 않습니다.

버전·CHANGELOG·태그·GitHub Release는 Release Please로 관리합니다. 버전 정책·초기 기준·활성화·복구는 [릴리스 안내](.github/RELEASING.md#한국어)를 따릅니다. 일반 기능·수정 PR에서 버전을 직접 올리지 않습니다.
