import type { paths } from "./api";

type Verify = paths["/api/v1/auth/code/verify"]["post"];
type AuthPolicy = paths["/api/v1/auth/policy"]["get"]["responses"][200]["content"]["application/json"];
type VerifyBody = Verify["requestBody"]["content"]["application/json"];
type Session = Verify["responses"][200]["content"]["application/json"];
type PutVault = paths["/api/v1/vault"]["put"]["requestBody"]["content"]["application/json"];
type SyncWrite = paths["/api/v1/sync/push"]["post"]["requestBody"]["content"]["application/json"];
type ChangePage = paths["/api/v1/sync/changes"]["get"]["responses"][200]["content"]["application/json"];
type SnapshotPage = paths["/api/v1/sync/snapshot"]["get"]["responses"][200]["content"]["application/json"];
type Deletion = paths["/api/v1/account"]["delete"]["responses"][202]["content"]["application/json"];
type AccountUsage = paths["/api/v1/account/usage"]["get"]["responses"][200]["content"]["application/json"];
type AdminStats = paths["/api/v1/admin/stats"]["get"]["responses"][200]["content"]["application/json"];
type UpdateSubscription = paths["/api/v1/admin/users/{user_id}/subscription"]["put"]["requestBody"]["content"]["application/json"];

export const signIn: VerifyBody = { email: "person@example.com", code: "123456" };
export const authPolicy: AuthPolicy = {
  domain_groups: { DEFAULT: ["gmail.com"], KR: ["naver.com"], JP: ["yahoo.co.jp"] },
  default_email_locale: "en",
  existing_accounts_allowed: true,
  blocked_local_part_characters: ["+", "."],
};
export function sessionUser(session: Session): string {
  return session.user_id;
}
export function nextVaultVersion(vault: PutVault): number {
  return vault.expected_version + 1;
}
export function nextRevision(operation: SyncWrite): number {
  return operation.base_revision + 1;
}
export function resumeFromSnapshot(page: SnapshotPage): string {
  return page.resume_cursor;
}
export function changesCursor(page: ChangePage): string {
  return page.next_cursor;
}
export function deletionAuthorization(receipt: Deletion): string {
  return `Bearer ${receipt.deletion_token}`;
}
export function remainingStorage(usage: AccountUsage): number {
  return Math.max(0, usage.storage_limit_bytes - usage.storage_bytes);
}
export function activePlan(usage: AccountUsage): "free" | "pro" {
  return usage.plan ?? "free";
}
export function proSubscriberCount(stats: AdminStats): number {
  return stats.subscriptions.pro_users;
}
export const grantPro: UpdateSubscription = {
  plan: "pro",
  status: "active",
  cancel_at_period_end: false,
  provider: "manual",
};
