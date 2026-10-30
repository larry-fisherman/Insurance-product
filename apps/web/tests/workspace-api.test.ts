import { webcrypto } from "node:crypto";
import { beforeEach, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  vi.stubGlobal("crypto", webcrypto);
  sessionStorage.clear();
});

it("recovers a committed write after a dropped response with the same key and payload", async () => {
  const committed = new Map<string, string>();
  const calls: { key: string; body: unknown }[] = [];
  const fetcher = vi.fn(async (_url: unknown, init: RequestInit) => {
    const key = new Headers(init.headers).get("Idempotency-Key")!;
    calls.push({ key, body: init.body });
    if (!committed.has(key)) {
      committed.set(key, "account-1");
      throw new TypeError("Response lost after commit");
    }
    return { ok: true, json: async () => ({ id: committed.get(key) }) };
  });
  vi.stubGlobal("fetch", fetcher);
  const { send } = await import("../src/lib/workspace-api");
  await expect(send("/insureds", { name: "Fictional LLC" })).rejects.toThrow(
    "Unable to connect",
  );
  expect(await send("/insureds", { name: "Fictional LLC" })).toEqual({ id: "account-1" });
  expect(calls[1]).toEqual(calls[0]);
  expect(committed.size).toBe(1);
  // A subsequent successful submission is a new logical operation.
  await expect(send("/insureds", { name: "Fictional LLC" })).rejects.toThrow();
  expect(calls[2].key).not.toBe(calls[0].key);
});

it("keeps the key across reloads and concurrent retries", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: unknown, init: RequestInit) => {
      calls.push(new Headers(init.headers).get("Idempotency-Key")!);
      throw new TypeError("offline");
    }),
  );
  let api = await import("../src/lib/workspace-api");
  await expect(api.send("/submissions", { title: "Renewal" })).rejects.toThrow();
  vi.resetModules();
  api = await import("../src/lib/workspace-api");
  await Promise.allSettled([
    api.send("/submissions", { title: "Renewal" }),
    api.send("/submissions", { title: "Renewal" }),
  ]);
  expect(new Set(calls).size).toBe(1);
});

it("fingerprints file contents and replacement lineage for upload recovery", async () => {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: unknown, init: RequestInit) => {
      calls.push(new Headers(init.headers).get("Idempotency-Key")!);
      throw new TypeError("offline");
    }),
  );
  const { api } = await import("../src/lib/workspace-api");
  // Supply the standard File.arrayBuffer method missing from jsdom.
  const BrowserFile = File;
  vi.stubGlobal(
    "File",
    class extends BrowserFile {
      arrayBuffer(): Promise<ArrayBuffer> {
        return new Promise((resolve) => {
          const reader = new FileReader();
          reader.onload = () => resolve(reader.result as ArrayBuffer);
          reader.readAsArrayBuffer(this);
        });
      }
    },
  );
  const body = new FormData();
  body.set("file", new File(["%PDF-fictional"], "test.pdf", { type: "application/pdf" }));
  body.set("supersedes_id", "prior-1");
  await expect(
    api("/submissions/1/documents", { method: "POST", body }),
  ).rejects.toThrow();
  await expect(
    api("/submissions/1/documents", { method: "POST", body }),
  ).rejects.toThrow();
  expect(calls[1]).toBe(calls[0]);
  body.set("supersedes_id", "prior-2");
  await expect(
    api("/submissions/1/documents", { method: "POST", body }),
  ).rejects.toThrow();
  expect(calls[2]).not.toBe(calls[0]);
});

it("shares an in-flight write result with concurrent callers", async () => {
  let complete!: (value: unknown) => void;
  const response = new Promise((resolve) => {
    complete = resolve;
  });
  const fetcher = vi.fn(() => response);
  vi.stubGlobal("fetch", fetcher);
  const { send } = await import("../src/lib/workspace-api");
  const first = send("/insureds", { name: "Concurrent retry LLC" });
  const second = send("/insureds", { name: "Concurrent retry LLC" });
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
  // Let both asynchronous payload hashes resolve before the response arrives.
  await new Promise((resolve) => setTimeout(resolve, 20));
  complete({ ok: true, json: async () => ({ id: "one-committed-record" }) });
  expect(await Promise.all([first, second])).toEqual([
    { id: "one-committed-record" },
    { id: "one-committed-record" },
  ]);
  expect(fetcher).toHaveBeenCalledOnce();
});
