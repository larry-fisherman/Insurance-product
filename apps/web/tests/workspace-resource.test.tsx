import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { ApiError, api } from "../src/lib/workspace-api";
import { useResource } from "../src/components/workspace/primitives";

const { push, router } = vi.hoisted(() => {
  const push = vi.fn();
  return { push, router: { push } };
});
vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("../src/lib/workspace-api", async (original) => ({
  ...(await original<typeof import("../src/lib/workspace-api")>()),
  api: vi.fn(),
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.mocked(api).mockReset();
  push.mockReset();
});

it("does not replace a post-save revision with an earlier poll response", async () => {
  const earlier = deferred<{ version: number }>();
  const saved = deferred<{ version: number }>();
  vi.mocked(api).mockReturnValueOnce(earlier.promise).mockReturnValueOnce(saved.promise);
  const { result } = renderHook(() =>
    useResource<{ version: number }>("/submissions/fictional"),
  );
  let refreshed!: Promise<void>;
  act(() => {
    refreshed = result.current.refresh();
  });
  await act(async () => {
    saved.resolve({ version: 14 });
    await refreshed;
  });
  expect(result.current.data?.version).toBe(14);
  await act(async () => {
    earlier.resolve({ version: 13 });
    await earlier.promise;
  });
  expect(result.current.data?.version).toBe(14);
});

it("ignores late authentication errors from superseded requests", async () => {
  const earlier = deferred<unknown>();
  vi.mocked(api)
    .mockReturnValueOnce(earlier.promise)
    .mockResolvedValueOnce({ version: 14 });
  const { result } = renderHook(() => useResource("/submissions/fictional"));
  await act(async () => {
    await result.current.refresh();
  });
  await act(async () => {
    earlier.reject(new ApiError(401, "SESSION_EXPIRED", "Expired"));
  });
  expect(result.current.error).toBe("");
  expect(push).not.toHaveBeenCalled();
});

it("clears old record data on navigation and ignores requests from the prior path", async () => {
  const pending = deferred<unknown>();
  const next = deferred<unknown>();
  vi.mocked(api)
    .mockResolvedValueOnce({ account: "first" })
    .mockReturnValueOnce(pending.promise)
    .mockReturnValueOnce(next.promise);
  const { result, rerender } = renderHook(({ path }) => useResource(path), {
    initialProps: { path: "/first" },
  });
  await act(async () => {});
  expect(result.current.data).toEqual({ account: "first" });
  const oldRefresh = result.current.refresh;
  act(() => {
    void result.current.refresh();
  });
  rerender({ path: "/second" });
  expect(result.current.data).toBeNull();
  await act(async () => {
    pending.resolve({ account: "stale first" });
  });
  expect(result.current.data).toBeNull();
  await act(async () => {
    next.resolve({ account: "second" });
  });
  expect(result.current.data).toEqual({ account: "second" });
  await act(async () => {
    await oldRefresh();
  });
  expect(api).toHaveBeenCalledTimes(3);
  expect(result.current.data).toEqual({ account: "second" });
});

it("does not redirect after a resource unmounts", async () => {
  const pending = deferred<unknown>();
  vi.mocked(api).mockReturnValueOnce(pending.promise);
  const { unmount } = renderHook(() => useResource("/first"));
  unmount();
  await act(async () => {
    pending.reject(new ApiError(401, "SESSION_EXPIRED", "Expired"));
  });
  expect(push).not.toHaveBeenCalled();
});

it("does not let polling continually supersede a slow in-flight refresh", async () => {
  vi.useFakeTimers();
  const pending = deferred<unknown>();
  vi.mocked(api)
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValueOnce({ version: 2 });
  const { result, unmount } = renderHook(() => useResource("/first", true));
  try {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(api).toHaveBeenCalledTimes(1);
    await act(async () => {
      pending.resolve({ version: 1 });
    });
    expect(result.current.data).toEqual({ version: 1 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(api).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual({ version: 2 });
  } finally {
    unmount();
    vi.useRealTimers();
  }
});
