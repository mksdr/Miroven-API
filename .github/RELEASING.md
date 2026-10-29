# Releases

[English](#english) · [한국어](#한국어)

## English

### Release policy

The repository is one release unit, including root Docker/deployment changes. Release Please reads Conventional Commits on `main`, opens/updates a release PR, and updates `version.txt`, the manifest and package metadata together. Merge the release PR only after the required checks pass. Automatic release-PR merging is initially disabled; enable it separately only after proving required checks and migration/compatibility review work. A release PR merge creates the version tag; it does not publish npm/PyPI packages.

- `fix`, `perf`, `revert`: patch; `feat`: minor; any breaking marker: major.
- `docs`, `test`, `build`, `ci`, `refactor`, ordinary `chore`: no standalone release.
- Dependabot uses `chore(deps)` / `chore(deps-dev)`. An update needing immediate delivery must describe the actual fix using `fix(deps): ...`; routine tooling updates wait for another releasable change.
- The same bump policy applies before 1.0: breaking changes are explicit major releases. Product versions do not change API routes, wire/crypto/backup formats, or migration identifiers automatically.
- Keep the client/API versions independent. Cross-repository releases record tested commits, uncommitted changes, compatibility range and migration evidence in the PR/release notes.

### Activate on GitHub

Repository configuration is separate from committed files. Before merging the automation:

1. Create a GitHub App installed on the intended repository only (or both Miroven repositories). Grant repository **Contents: read/write**, **Pull requests: read/write**, **Issues: read/write**; no organization or administration permission is needed.
2. In each repository, set Actions variable `RELEASE_APP_ID` and Actions secret `RELEASE_APP_PRIVATE_KEY`. Enter the key through GitHub's secret UI; never commit it, paste it into an issue, or print it. The workflow requests a token scoped to the current repository. Do not substitute `GITHUB_TOKEN`: its bot PRs/tags do not trigger the required follow-up workflows.
3. Allow squash merging and use **PR title and description** for the default squash message. Disable merge/rebase methods if all final commits must comply. Keep `BREAKING CHANGE` explanations in the final message.
4. Run the new **Commit and release policy** check, then require it together with existing application, dependency-review and code-scanning gates in the `main` ruleset. Require the branch to be current (or an equivalent merge queue policy), prevent direct pushes, and do not give the release App a bypass of required checks.
5. Protect stable `v*` tags against deletion/movement; permit the release App to create them. Keep automatic PR merging disabled for the first release.
6. Merge the automation, run **Release Please** on `main`, and inspect its first PR. Missing App configuration intentionally fails with an actionable error. An all-chore history may correctly produce no release PR.

New action references are pinned to verified commit SHAs. Major/runtime action upgrades remain manual reviews.

### Validation and first release

Run from the repository root with Python 3.12 (standard library only):

```sh
python -m unittest discover -s .github/scripts -p 'test_*.py'
python .github/scripts/release_policy.py
# On a checked-out release tag:
python .github/scripts/release_policy.py --tag vX.Y.Z
```

The workflow also validates PR titles using the event JSON file, without interpolating titles into shell code. Scope is optional, Korean descriptions are accepted, and no local Git hook is required. Version mismatches block the policy check. For API tags, the existing image-publishing workflow also checks versions before publishing.

On a non-production test repository/branch, verify fix/minor/breaking/no-release cases, a root-only Docker change, release-PR updates after another commit, bot PR CI, tag workflow execution, and rerunning publication. Do not use a local unit-test result as evidence that GitHub App permissions, branch rules or registry publishing work.

Review pre-automation nonconventional commits manually in the first release PR; they cannot be reliably classified from their titles. Preserve historical tags and do not backfill speculative release notes. After the first automated release, remove the no-longer-needed `bootstrap-sha` in a normal PR.

### Recovery

If release generation fails, correct configuration/permissions and rerun **Release Please** on `main`. Before retrying a merged release PR, inspect its `autorelease: pending/tagged` labels and existing tag/release. Do not delete a published tag or change its target. If a published version is wrong, ship a new corrective version. If a release needs a different version, use the documented Release Please `Release-As` override in a reviewed PR rather than editing only one version file.

The client application, API integration, image builds, production SMTP/TLS/storage and physical-device behavior still need their normal checks; message/metadata tests do not establish them.

### API baseline and publication

Initial version: **0.1.0**. Bootstrap starts after the existing v0.1.0 commit `57c66fde38d3d4b29a2926894a6d1adf38d8d5be`. The pre-automation unreleased changes include TrueNAS provisioning hardening and email signup policy; review them in the first release notes.

Release Please updates `pyproject.toml`, only the local project's version in `uv.lock`, npm package/lock root versions, the annotated FastAPI version and OpenAPI `info.version`. OpenAPI metadata must equal fresh exporter output; no routes/schemas or client TypeScript contracts are changed by a product version bump. Keep `x-release-please-version` on the FastAPI version line. CI retains `uv sync --locked` and OpenAPI regeneration checks.

The pinned Release Please TOML updater wraps parsed values internally; its uv.lock selector deliberately uses `@.name.value`. Revalidate that selector with the actual updater when upgrading Release Please: exactly the local project version may change, and dependency versions must remain untouched.

Release Please creates a **draft** Release with a forced stable tag. The App-created tag triggers existing API **verify** and **compose-smoke** jobs. Only their success permits multi-architecture GHCR publishing. The publishing job preserves generated notes, uploads `deployment.json` containing the tested commit and immutable image digest, then publishes the existing draft as its final step. TrueNAS continues reading only published stable releases. An upload failure leaves a draft; rerun the failed API tag job. A successful rerun validates the already published descriptor and skips image publication. An incomplete or mismatched published release fails rather than overwriting it. Manual stable tag pushes remain supported only when tag and all metadata agree.

`api_release.py` uses the publishing job's scoped `GITHUB_TOKEN` for GHCR/releases; it does not need the App key. Actual GHCR/Compose/TrueNAS activation must be tested separately.

If an older tag finishes after a newer release, it is published with `--latest=false` so delayed jobs cannot move the latest-release pointer backwards.

## 한국어

### 릴리스 정책

루트 Docker·배포 변경까지 저장소 전체를 하나의 릴리스로 관리합니다. Release Please가 `main`의 Conventional Commits를 분석하고 릴리스 PR을 생성·갱신하며 `version.txt`·manifest·패키지 메타데이터를 함께 변경합니다. 필수 검사를 통과한 뒤 릴리스 PR을 병합하면 태그가 만들어집니다. npm/PyPI 배포는 추가하지 않습니다. 초기에는 릴리스 PR 자동 병합을 끄고, 필수 검사와 마이그레이션·호환성 검토가 검증된 후 별도로 활성화합니다.

- `fix`·`perf`·`revert`는 patch, `feat`는 minor, 호환성 파괴 표시는 major입니다.
- `docs`·`test`·`build`·`ci`·`refactor`·일반 `chore`만으로는 릴리스하지 않습니다.
- Dependabot은 `chore(deps)`·`chore(deps-dev)`를 사용합니다. 즉시 배포할 수정은 실제 문제를 설명하는 `fix(deps): ...`로 병합하고 일반 도구 갱신은 다음 릴리스에 포함합니다.
- 1.0 미만에도 같은 증가 규칙을 적용합니다. 제품 버전 증가는 API 경로·통신·암호화·백업 형식이나 마이그레이션 식별자를 자동 변경하지 않습니다.
- 클라이언트와 API는 독립 버전입니다. 연동 릴리스에는 테스트한 두 커밋, 관련 미커밋 변경, 호환 범위와 마이그레이션 검증을 PR·릴리스 설명에 남깁니다.

### GitHub 활성화

저장소 설정은 커밋된 파일과 별개입니다. 자동화 PR을 병합하기 전에 다음을 준비합니다.

1. 대상 저장소에만 설치한 GitHub App을 만듭니다. 두 Miroven 저장소를 함께 선택해도 됩니다. 저장소 Contents·Pull requests·Issues에 읽기/쓰기를 부여하며 조직·관리자 권한은 필요하지 않습니다.
2. 각 저장소에 Actions 변수 `RELEASE_APP_ID`와 secret `RELEASE_APP_PRIVATE_KEY`를 설정합니다. 키는 GitHub secret 화면에서 입력하고 커밋·이슈·로그에 남기지 않습니다. 워크플로는 현재 저장소 범위의 토큰을 요청합니다. 기본 `GITHUB_TOKEN`으로 대체하면 봇 PR·태그에 필요한 후속 워크플로가 실행되지 않습니다.
3. squash 병합과 기본 메시지 **PR title and description**을 설정합니다. 최종 커밋 전체를 통일하려면 merge/rebase 병합을 끕니다. 최종 메시지에 `BREAKING CHANGE` 설명을 보존합니다.
4. **Commit and release policy**를 실행한 다음 기존 앱·의존성·코드 스캔 검사와 함께 `main` ruleset의 필수 검사로 지정합니다. 최신 브랜치 검사 또는 동등한 merge queue 정책을 적용하고 직접 push를 막습니다. 릴리스 App에도 필수 검사 우회를 허용하지 않습니다.
5. 안정 버전 `v*` 태그의 삭제·이동을 보호하고 릴리스 App의 새 태그 생성을 허용합니다. 첫 릴리스는 PR 자동 병합을 끕니다.
6. 설정 PR을 병합하고 `main`에서 **Release Please**를 실행해 첫 PR을 확인합니다. App 설정이 없으면 해결 안내와 함께 실패합니다. `chore`만 있으면 릴리스 PR이 생기지 않는 것이 정상입니다.

새 Action은 확인한 커밋 SHA로 고정합니다. major·런타임 업그레이드는 수동 검토합니다.

### 검증과 첫 릴리스

저장소 루트에서 Python 3.12로 실행합니다. 표준 라이브러리만 사용합니다.

```sh
python -m unittest discover -s .github/scripts -p 'test_*.py'
python .github/scripts/release_policy.py
python .github/scripts/release_policy.py --tag vX.Y.Z
```

마지막 명령은 해당 태그를 checkout한 상태에서 사용합니다. PR 제목은 이벤트 JSON 파일로 읽으며 셸 코드에 삽입하지 않습니다. scope는 선택 사항이고 한국어 요약을 허용하며 로컬 Git hook은 필수가 아닙니다. 버전 불일치는 검사를 실패시킵니다. API 이미지 발행 작업도 태그 버전을 별도로 검사합니다.

운영과 분리한 테스트 저장소·브랜치에서 patch/minor/major/릴리스 없음, 루트 Docker 단독 변경, 후속 커밋에 따른 릴리스 PR 갱신, 봇 PR CI, 태그 워크플로, 발행 재실행을 검증합니다. 로컬 단위 테스트는 App 권한·브랜치 규칙·레지스트리 발행의 증거가 아닙니다.

도입 전 일반 문장형 커밋은 제목만으로 분류할 수 없으므로 첫 릴리스 PR에서 누락을 수동 검토합니다. 기존 태그를 보존하고 추측한 과거 릴리스 노트를 만들지 않습니다. 첫 자동 릴리스 후 불필요해진 `bootstrap-sha`는 일반 PR에서 제거합니다.

### 실패 복구

릴리스 생성 실패는 권한·설정을 고친 뒤 `main`에서 **Release Please**를 재실행합니다. 병합된 릴리스 PR을 재시도하기 전 `autorelease: pending/tagged` 라벨과 기존 태그·Release를 확인합니다. 공개된 태그를 삭제·이동하지 않고 잘못된 공개 버전은 새 버전으로 수정합니다. 다음 버전 조정은 검토한 PR의 Release Please `Release-As` 지시를 사용하고 일부 버전 파일만 수정하지 않습니다.

앱·실제 API 연동·이미지 빌드·운영 SMTP/TLS/스토리지·실기기 검증은 기존 절차대로 필요합니다. 메시지·메타데이터 테스트가 이를 대신하지 않습니다.

### API 초기 기준과 공개 순서

초기 버전은 **0.1.0**, 비교 기준은 기존 v0.1.0 커밋 `57c66fde38d3d4b29a2926894a6d1adf38d8d5be`입니다. 도입 전 미릴리스 변경인 TrueNAS 인증정보 설정 보강과 가입 이메일 정책을 첫 릴리스 노트에서 확인합니다.

`pyproject.toml`, `uv.lock`의 프로젝트 자체 버전, npm 패키지·lock 루트 버전, 주석으로 표시한 FastAPI 버전과 OpenAPI `info.version`을 함께 갱신합니다. OpenAPI 메타데이터는 exporter 재생성과 일치해야 합니다. 제품 버전만 바뀌면 경로·스키마와 클라이언트 TypeScript 계약은 바뀌지 않습니다. FastAPI 버전 줄의 `x-release-please-version` 주석을 유지합니다. 기존 `uv sync --locked`와 OpenAPI 최신성 검사는 유지합니다.

고정한 Release Please의 TOML updater는 내부에서 파싱 값을 감싸므로 uv.lock 선택자는 의도적으로 `@.name.value`를 사용합니다. Release Please 업그레이드 시 실제 updater로 프로젝트 자체 버전만 바뀌고 의존성 버전이 보존되는지 다시 검증합니다.

Release Please는 **초안 Release와 안정 태그**를 만듭니다. App이 만든 태그가 API의 기존 **verify**·**compose-smoke**를 실행하고 두 검사 성공 후에만 다중 아키텍처 GHCR 이미지를 발행합니다. 생성된 릴리스 노트를 보존하고 검증 커밋·불변 이미지 digest가 담긴 `deployment.json`을 업로드한 뒤 마지막 단계에서 기존 초안을 공개합니다. TrueNAS는 계속 공개된 안정 Release만 읽습니다. 업로드 실패 시 초안으로 남으며 실패한 API 태그 작업을 재실행합니다. 이미 공개된 정상 릴리스는 descriptor를 확인하고 이미지 발행을 건너뜁니다. 불완전하거나 일치하지 않는 공개 릴리스는 덮어쓰지 않고 실패합니다. 수동 안정 태그도 모든 버전과 일치할 때 지원합니다.

발행 도구 `api_release.py`는 발행 작업의 `GITHUB_TOKEN`을 사용하며 App 키를 요구하지 않습니다. 실제 GHCR·Compose·TrueNAS 동작은 별도로 검증합니다.

오래된 태그의 작업이 새 릴리스보다 늦게 끝나면 `--latest=false`로 공개해 최신 릴리스 포인터가 과거 버전으로 돌아가지 않게 합니다.
