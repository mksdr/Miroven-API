# Contributing

[English](#english) · [한국어](#한국어)

## English

Use the project's [issue tracker](https://github.com/mksdr/Miroven/issues) for ordinary bugs and proposals, identifying **Miroven-API** as the affected component. Include the API version/commit, deployment mode, expected/actual behavior, and synthetic reproduction steps. Discuss substantial changes before implementing them. Report vulnerabilities through [SECURITY.md](SECURITY.md), not a public issue.

Follow [API development](https://github.com/mksdr/Miroven/wiki/API-Development) for setup and checks. Database tests clear tables: use only a dedicated database whose name ends in `_test`. Never run them against production or your ordinary development data.

Keep pull requests focused, explain behavior and compatibility implications, and report checks run or unavailable. Changes to routes or schemas must update generated OpenAPI/TypeScript contracts and the client's copied contract. Preserve revision, idempotency, cursor, ownership, deletion, and backup guarantees. Add relevant regression tests and migrations when needed, and keep the English/Korean Wiki pages aligned.

Never include `.env`, live credentials, SMTP payloads, session tokens, recovery codes, or personal records in reports or commits. Preserve copyright and third-party notices; contributions follow [AGPL-3.0-only](LICENSE).

## 한국어

일반 버그와 제안은 프로젝트 [이슈](https://github.com/mksdr/Miroven/issues)에 **Miroven-API** 문제임을 표시하고 작성하세요. API 버전/커밋, 배포 방식, 예상·실제 동작, 합성 데이터 재현 순서를 포함하세요. 큰 변경은 구현 전에 논의하고, 취약점은 공개 이슈 대신 [SECURITY.md](SECURITY.md#한국어)로 신고하세요.

설치·검증은 [API 개발 안내](https://github.com/mksdr/Miroven/wiki/API-Development.ko)를 따릅니다. DB 테스트는 테이블을 비우므로 이름이 `_test`로 끝나는 전용 DB만 사용하세요. 운영 DB나 일반 개발 데이터를 대상으로 실행하면 안 됩니다.

PR은 한 목적에 집중하고 동작·호환성 영향과 실행한 검사, 실행하지 못한 검사를 적어 주세요. 경로·스키마 변경 시 생성 OpenAPI/TypeScript 계약과 클라이언트 복사본을 갱신합니다. revision·멱등성·커서·소유권·삭제·백업 보장을 보존하고 필요한 회귀 테스트와 마이그레이션을 추가하세요. Wiki의 영어·한국어 문서도 함께 갱신합니다.

`.env`·실제 인증정보·메일 payload·세션 토큰·복구 코드·개인 기록을 제보나 커밋에 포함하지 마세요. 저작권과 타사 고지를 유지하며 기여에는 [AGPL-3.0-only](LICENSE)가 적용됩니다.
