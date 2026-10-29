# Sync database budgets

## Account-wide sync database budgets

Migration `0006_sync_budgets` is part of the current required Alembic head. It backfills the UTF-8 JSON byte count of existing current documents and change history without deleting content or idempotency receipts. Stop API/worker writers and take a paired backup before upgrade. The backfill scans existing documents and locks `sync_states`; allow a maintenance window on large databases. Downgrade removes only the new accounting columns, retaining journal data, but the old server no longer enforces these protections. Re-upgrade recalculates usage and starts a new hourly window.

These positive account-wide operational settings apply to every plan, independently of blob quotas and subscription overrides:

| Setting | Default | Meaning |
| --- | --- | --- |
| `MIROVEN_SYNC_STORAGE_QUOTA_BYTES` | 134217728 (128 MiB) | Logical JSON bytes in current entry/profile/tombstone documents plus retained change history |
| `MIROVEN_SYNC_OPERATION_LIMIT` | 1000000 | Lifetime accepted operations/receipts, using the monotonically increasing sync head |
| `MIROVEN_SYNC_HOURLY_OPERATIONS` | 20000 | Successful new operations per UTC clock hour |
| `MIROVEN_SYNC_HOURLY_BYTES` | 67108864 (64 MiB) | Successful new documents' logical JSON ingress bytes per UTC clock hour |

Document bytes use UTF-8 JSON with the same separators as PostgreSQL `JSONB::text` for the validated sync schema; they include base64 ciphertext, envelope fields, IDs, revisions, metadata, and attachment IDs. A write replaces the current document and adds a full history copy, so charge `2 * new_document_bytes - previous_current_document_bytes`. This is not compressed database/index/WAL/disk usage. `/account/usage.storage_bytes` remains blob-only. No request, response, encryption, AAD, revision, or cursor encoding changes are introduced.

The authenticated user lock serializes reservations, new documents/history/receipts, and maintenance. Commit/validation/storage-reference failures roll back the reservation. Exact operation-ID retries return their original receipt before checking any budgets and do not consume another slot; mismatched retries still return `operation_mismatch`. Receipt records remain for the account lifetime: they are not silently evicted. On reaching the lifetime cap, new writes (including deletion operations) require an operator to review/raise configuration; history cleanup does not reset the sync head. Account deletion remains available.

New growth exceeding the byte budget returns 409 `sync_storage_limit`; the receipt cap returns 409 `sync_operation_limit`; the hourly count or byte cap returns 429 `sync_rate_limited`. The existing client retains the pending operation for these errors, rather than treating them as revision conflicts or acknowledging them. Read access and snapshot recovery continue at a write limit. Hourly limits reset at UTC hour boundaries; a write larger than the entire hourly byte budget needs a configuration change. Defaults allow large offline queues to drain over multiple hours.

Retention cleanup releases only pruned history bytes under the same lock and preserves receipts/cursors/blob references. Lowered limits or migrated over-limit data are not deleted; net byte-reducing writes may proceed while over the document quota, subject to the receipt/hourly caps. Current-document bytes cannot be reclaimed by aging history. Adjust capacity after reviewing real usage and keep originals/backups.

Snapshot copies are excluded from the primary write budget so recovery remains usable. Each frozen copy retains at most the current-document bytes present when it is created, with at most `max_snapshots` copies per account (default five, TTL one hour). Lowering the budget or shrinking documents does not shrink existing snapshots before expiry, and grandfathered over-limit data can produce larger snapshots. Retention and expired-snapshot cleanup must keep running. Receipt metadata is bounded by the lifetime operation cap and fixed-size validated result fields. Actual total disk use and aggregate usage across accounts still need operational monitoring/capacity management.


## 계정별 동기화 DB 예산

현재 서버는 Alembic head에 포함된 `0006_sync_budgets`가 필요하다. 기존 현재 문서와 변경 이력의 UTF-8 JSON 바이트 사용량을 역산하며 콘텐츠·재시도 영수증은 지우지 않는다. API/worker 쓰기를 중지하고 DB·blob 쌍 백업 후 업그레이드한다. 기존 문서를 스캔하고 `sync_states`를 잠그므로 큰 DB에는 점검 시간을 확보한다. downgrade는 새 집계 열만 제거해 기록은 보존하지만 구버전 서버에는 보호 한도가 없다. 다시 upgrade하면 사용량을 재계산하고 새 시간 구간을 시작한다.

다음 양수 설정은 blob 한도·구독별 재정의와 독립적으로 모든 플랜의 각 계정에 적용된다.

| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| `MIROVEN_SYNC_STORAGE_QUOTA_BYTES` | 134217728 (128 MiB) | 현재 기록·프로필·tombstone 및 보존 변경 이력의 논리 JSON 바이트 |
| `MIROVEN_SYNC_OPERATION_LIMIT` | 1000000 | 단조 증가 sync head로 집계한 계정 수명 전체의 승인 operation/영수증 수 |
| `MIROVEN_SYNC_HOURLY_OPERATIONS` | 20000 | UTC 시계 시간당 성공한 새 operation 수 |
| `MIROVEN_SYNC_HOURLY_BYTES` | 67108864 (64 MiB) | UTC 시계 시간당 성공한 새 문서의 논리 JSON 유입 바이트 |

문서 바이트는 검증된 sync 스키마에서 PostgreSQL `JSONB::text`와 같은 구분자를 쓰는 UTF-8 JSON 크기다. base64 암호문·봉투·ID·revision·메타데이터·첨부 ID를 포함한다. 쓰기는 현재 문서를 교체하고 이력 전체 사본을 추가하므로 `2 * 새 문서 바이트 - 이전 현재 문서 바이트`를 반영한다. 압축된 DB·인덱스·WAL·실제 디스크 크기는 아니며 `/account/usage.storage_bytes`는 계속 blob만 집계한다. 요청·응답·암호화·AAD·revision·cursor 형식은 유지한다.

사용자 행 잠금 아래 예약·문서·이력·영수증 및 유지보수를 직렬화한다. commit·검증·파일 참조 실패는 예약도 rollback한다. 동일 operation-ID의 정확한 재시도는 모든 한도 검사 전에 원래 영수증을 반환하며 예산을 다시 사용하지 않는다. 다른 payload의 재시도는 계속 `operation_mismatch`다. 영수증은 계정 수명 동안 유지하며 임의로 제거하지 않는다. 수명 한도에 도달하면 삭제 operation을 포함한 새 쓰기는 운영자가 사용량을 검토하고 설정을 높여야 한다. 이력 정리는 head를 초기화하지 않으며 계정 삭제는 계속 가능하다.

바이트 한도를 초과하는 증가는 409 `sync_storage_limit`, 영수증 한도는 409 `sync_operation_limit`, 시간당 수·바이트 한도는 429 `sync_rate_limited`다. 기존 클라이언트는 이를 revision 충돌이나 승인으로 처리하지 않고 pending operation을 보존한다. 쓰기 한도에 도달해도 읽기와 snapshot 복구는 가능하다. 시간당 한도는 UTC 정시에 초기화되며 단일 문서가 시간당 바이트 예산 전체보다 크면 설정 변경이 필요하다. 큰 오프라인 큐는 여러 시간에 걸쳐 전송할 수 있다.

보존 기간이 지난 이력만 같은 잠금 아래 제거하고 해당 바이트를 반환한다. 영수증·커서·파일 참조는 보존한다. 설정을 낮추거나 기존 데이터가 새 한도보다 커도 콘텐츠를 지우지 않으며, 논리 바이트를 줄이는 쓰기는 영수증·시간당 한도 내에서 허용한다. 현재 문서가 차지하는 공간은 이력 만료만으로 반환되지 않는다. 실사용량을 검토해 용량을 조정하고 원본·백업을 유지한다.

복구를 위해 snapshot 사본은 기본 쓰기 예산에서 제외한다. 각 고정 사본은 생성 시점의 현재 문서 바이트만 복사하며 계정당 최대 `max_snapshots`개다(기본 5개, TTL 1시간). 한도를 낮추거나 현재 문서를 줄여도 기존 snapshot은 만료 전에 작아지지 않으며, 기존 초과 데이터는 더 큰 snapshot을 만들 수 있다. 이력 및 만료 snapshot 정리를 계속 실행해야 한다. 영수증 메타데이터는 수명 operation 한도와 검증된 고정 크기 결과 필드로 제한한다. 실제 전체 디스크 사용량과 여러 계정의 합계는 별도로 용량 관리·모니터링한다.

## Wiki publication

The matching English/Korean Wiki updates are staged in `wiki/sync-budgets.patch` for review and publication with the released API. Live Wiki pages have not been published from this PR.
