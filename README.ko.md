# Miroven API

[English](README.md) · **한국어**

Miroven의 선택적 이메일 인증·저널 및 프로필 암호문 동기화·파일 저장 서버입니다. Python 3.12, PostgreSQL, SMTP와 메일·정리를 담당하는 worker로 구성됩니다. 기록 암호화와 금고 복구는 클라이언트가 담당하며, 서버는 암호문과 운영 메타데이터를 저장합니다.

## 시작하기

Docker Compose Linux 엔진이 필요합니다. 저장소 루트에서 `.env`가 없을 때만 예제를 복사합니다.

```sh
cp .env.example .env
docker compose up -d --build --wait
```

API는 `http://localhost:8000`, 대화형 API 명세는 `/docs`, 개발 Mailpit은 `http://localhost:8025`입니다. 마이그레이션 후 API와 worker가 시작합니다. `docker compose down`으로 종료하면 볼륨이 보존됩니다. 기본 구성은 개발용이며 실제 호스팅은 운영 배포 안내를 따르세요.

## 문서

| 목적 | 안내 |
| --- | --- |
| 개발·검증 | [API 개발](https://github.com/mksdr/Miroven/wiki/API-Development.ko) |
| 자체 호스팅·클라이언트 연결 | [API 배포](https://github.com/mksdr/Miroven/wiki/API-Deployment.ko) |
| 삭제·백업·복구·업그레이드 | [API 운영](https://github.com/mksdr/Miroven/wiki/API-Operations.ko) |
| 인증·보안 경계 | [API 인증](https://github.com/mksdr/Miroven/wiki/API-Authentication.ko) |
| 동기화·프로필·커서·파일·S3 | [동기화 계약](https://github.com/mksdr/Miroven/wiki/API-Sync-Protocol.ko) |
| 검증 한계와 공개 전 점검 | [검증 범위](https://github.com/mksdr/Miroven/wiki/API-Validation.ko) |
| 정확한 요청·응답 타입 | [OpenAPI](contracts/openapi.json), [TypeScript](contracts/api.d.ts) |

기술 문서는 [mksdr/Miroven Wiki](https://github.com/mksdr/Miroven/wiki/Home.ko)로 통합합니다. 클라이언트·API·홈페이지의 패키지 버전은 별도로 관리하며 공식 호스팅 서비스의 이용 가능 여부는 별도로 안내합니다.

## 기여와 문제 제보

일반 버그와 기여는 [CONTRIBUTING.md](CONTRIBUTING.md#한국어), 취약점 비공개 신고는 [SECURITY.md](SECURITY.md#한국어)를 참고하세요. 신고에 실제 기록·복구 코드·인증정보·세션 토큰을 포함하지 마세요.

## 라이선스

[GNU AGPL-3.0-only](LICENSE). 타사 의존성에는 각 라이선스가 적용됩니다.
