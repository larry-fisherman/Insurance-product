export type Json = string | number | boolean | null | Json[] | { [key: string]: Json };
export interface Entity {
  id: string;
  created_at: string;
  tenant_id: string;
}
export interface Me {
  user_id: string;
  name: string;
  email: string | null;
  tenant_id: string;
  membership_id: string;
  roles: string[];
  permissions: string[];
  mfa_verified: boolean;
  auth_method: string;
  agencies: { id: string; name: string }[];
}
export interface Insured extends Entity {
  name: string;
  dba: string | null;
  entity_type: string | null;
  operations: string | null;
  mailing_address: string | null;
  external_reference: string | null;
  status: string;
  row_version: number;
}
export interface Submission extends Entity {
  insured_id: string;
  title: string;
  kind: string;
  intended_start: string;
  intended_end: string;
  due_date: string | null;
  loss_required_start: string | null;
  loss_required_end: string | null;
  status: string;
  input_version: number;
  row_version: number;
  revision_number: number;
  assigned_membership_id: string;
  prior_submission_id: string | null;
  requirements: { loss_freshness_days?: number };
  insured_name?: string;
}
export interface Document extends Entity {
  filename: string;
  media_type: string;
  sha256: string;
  scan_status: string;
  processing_status: string;
  document_type: string;
  page_count: number;
  excluded_reason: string | null;
  supersedes_id: string | null;
  byte_size: number;
}
export interface Job extends Entity {
  status: string;
  stage: string;
  kind: string;
  document_id: string | null;
  error_code: string | null;
  attempts: number;
}
export interface Source extends Entity {
  document_id: string;
  text: string;
  locator: {
    kind: string;
    page?: number;
    line?: number;
    sheet?: string;
    range?: string;
    method?: string;
    warnings?: string[];
  };
}
export interface Observation extends Entity {
  field: string;
  value: Json;
  context: Record<string, Json>;
  source_id: string;
  supporting_source_ids: string[];
  document_id: string;
  method: string;
  warnings: string[];
}
export interface Decision {
  action: string;
  value: Json;
  context: Record<string, Json>;
  reason: string;
  observation_id: string | null;
  field: string;
}
export interface Group {
  key: string;
  field: string;
  label: string;
  context: Record<string, Json>;
  observations: Observation[];
  fingerprint: string;
  conflicting: boolean;
  verified: boolean;
  state: string;
  decision: Decision | null;
}
export interface Blocker {
  key: string;
  message: string;
  waivable: boolean;
  fingerprint?: string;
}
export interface ReviewState {
  ready: boolean;
  blockers: Blocker[];
  exceptions: { key: string; message: string; reason: string }[];
  groups: Group[];
  facts: {
    field: string;
    value: Json;
    context: Record<string, Json>;
    source_ids: string[];
  }[];
  verified_count: number;
  total_count: number;
}
export interface Package extends Entity {
  status: string;
  revision_id: string;
  input_version: number;
  manifest_hash: string | null;
  manifest: Record<string, Json>;
  template_version: string;
  approval?: { id: string; created_at: string } | null;
  superseded?: boolean;
}
export interface Task extends Entity {
  submission_id: string;
  title: string;
  description: string;
  status: string;
  due_date: string | null;
}
export interface WorkspaceData {
  submission: Submission;
  insured: Insured;
  documents: Document[];
  jobs: Job[];
  readiness: ReviewState;
  sources: Source[];
  packages: Package[];
  tasks: Task[];
  field_labels: Record<string, string>;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    const csrf = document.cookie
      .split("; ")
      .find((c) => c.startsWith("cgl_csrf="))
      ?.split("=")[1];
    if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf));
    headers.set("Idempotency-Key", headers.get("Idempotency-Key") ?? crypto.randomUUID());
  }
  let response: Response;
  try {
    response = await fetch("/api/v1" + path, {
      ...options,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      0,
      "NETWORK_ERROR",
      "Unable to connect. Check that the local workspace is running and try again.",
    );
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      data.detail?.code ?? "REQUEST_FAILED",
      data.detail?.message ?? "The request could not be completed. Please try again.",
    );
  }
  return response.json() as Promise<T>;
}
export const send = <T>(path: string, value: unknown, method = "POST") =>
  api<T>(path, { method, body: JSON.stringify(value) });
export const pretty = (value: Json | undefined): string =>
  value === undefined || value === null
    ? "Not supplied"
    : typeof value === "object"
      ? JSON.stringify(value, null, 2)
      : String(value);
export const label = (value: string) => value.replaceAll("_", " ").replaceAll(".", " ");
export const dateLabel = (value: string | null) =>
  value
    ? new Date(value.slice(0, 10) + "T12:00:00").toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      })
    : "Not set";
