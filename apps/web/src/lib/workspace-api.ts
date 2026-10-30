import type { components } from "@cgl/contracts";

export type Json = components["schemas"]["JsonValue"];
export type Entity = Pick<
  components["schemas"]["InsuredResponse"],
  "id" | "tenant_id" | "created_at"
>;
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
export type Insured = components["schemas"]["InsuredResponse"];
export type Submission = components["schemas"]["SubmissionResponse"];
export type Document = components["schemas"]["DocumentResponse"];
export type Job = components["schemas"]["JobResponse"];
export type Source = components["schemas"]["SourceResponse"];
export type Observation = components["schemas"]["ObservationResponse"];
export type Decision = components["schemas"]["DecisionResponse"];
export type Group = components["schemas"]["GroupResponse"];
export type Blocker = components["schemas"]["BlockerResponse"];
export type ReviewState = components["schemas"]["ReviewStateResponse"];
export type Package = components["schemas"]["PackageResponse"];
export type Task = components["schemas"]["TaskResponse"];
export type WorkspaceData = components["schemas"]["WorkspaceResponse"];

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

// Keep uncertain writes for the lifetime of the logical payload, including a reload.
// Only a digest and UUID enter sessionStorage; document contents stay in memory.
const pendingWrites = new Map<string, { key: string; body: BodyInit | null }>();
const inFlightWrites = new Map<string, Promise<unknown>>();
async function writeIdentity(path: string, options: RequestInit): Promise<string> {
  const pieces: string[] = [options.method ?? "POST", path];
  if (options.body instanceof FormData) {
    for (const [name, value] of options.body.entries()) {
      if (value instanceof File) {
        const hash = await crypto.subtle.digest("SHA-256", await value.arrayBuffer());
        pieces.push(
          name,
          value.name,
          value.type,
          Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join(
            "",
          ),
        );
      } else pieces.push(name, value);
    }
  } else pieces.push(String(options.body ?? ""));
  pieces.push(document.cookie.split("; ").find((c) => c.startsWith("cgl_csrf=")) ?? "");
  const hash = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(JSON.stringify(pieces)),
  );
  return (
    "cgl-pending:" +
    Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join("")
  );
}
function storedKey(identity: string): string | null {
  try {
    return sessionStorage.getItem(identity);
  } catch {
    return null;
  }
}
function storeKey(identity: string, key: string | null) {
  try {
    if (key) sessionStorage.setItem(identity, key);
    else sessionStorage.removeItem(identity);
  } catch {
    /* In-memory recovery still works when storage is unavailable. */
  }
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  let identity: string | undefined;
  let body = options.body;
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (options.method && !["GET", "HEAD"].includes(options.method)) {
    const csrf = document.cookie
      .split("; ")
      .find((c) => c.startsWith("cgl_csrf="))
      ?.split("=")[1];
    if (csrf) headers.set("X-CSRF-Token", decodeURIComponent(csrf));
    if (!headers.has("Idempotency-Key")) {
      identity = await writeIdentity(path, options);
      let pending = pendingWrites.get(identity);
      if (!pending) {
        const snapshot =
          options.body instanceof FormData ? new FormData() : (options.body ?? null);
        if (snapshot instanceof FormData && options.body instanceof FormData)
          for (const [name, value] of options.body.entries())
            snapshot.append(name, value);
        pending = { key: storedKey(identity) ?? crypto.randomUUID(), body: snapshot };
        pendingWrites.set(identity, pending);
        storeKey(identity, pending.key);
      }
      headers.set("Idempotency-Key", pending.key);
      body = pending.body;
    }
  }
  if (identity && inFlightWrites.has(identity))
    return inFlightWrites.get(identity) as Promise<T>;
  const perform = async (): Promise<T> => {
    let response: Response;
    try {
      response = await fetch("/api/v1" + path, {
        ...options,
        body,
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
      if (identity && response.status < 500 && ![408, 429].includes(response.status)) {
        pendingWrites.delete(identity);
        storeKey(identity, null);
      }
      const data = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        data.detail?.code ?? "REQUEST_FAILED",
        data.detail?.message ?? "The request could not be completed. Please try again.",
      );
    }
    let result: T;
    try {
      result = (await response.json()) as T;
    } catch {
      throw new ApiError(
        0,
        "UNCERTAIN_RESPONSE",
        "The response was interrupted. Retry to recover the same operation.",
      );
    }
    if (identity) {
      pendingWrites.delete(identity);
      storeKey(identity, null);
    }
    return result;
  };
  if (!identity) return perform();
  const request = perform();
  inFlightWrites.set(identity, request);
  try {
    return await request;
  } finally {
    if (inFlightWrites.get(identity) === request) inFlightWrites.delete(identity);
  }
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
