import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConnectionStatus } from "../src/components/connection-status";

const ready = {
  status: "ready",
  service: "cgl-api",
  version: "0.1.0",
  scope: "application",
};

describe("workspace connection", () => {
  it("uses the same-origin API route and reports readiness", async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, json: async () => ready });
    vi.stubGlobal("fetch", request);
    render(<ConnectionStatus />);
    expect(
      await screen.findByText("Connected. Your workspace is available."),
    ).toBeVisible();
    expect(request).toHaveBeenCalledWith(
      "/api/v1/health/ready",
      expect.objectContaining({ cache: "no-store", signal: expect.any(AbortSignal) }),
    );
  });

  it("offers retry after a failed request and recovers", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ ok: true, json: async () => ready });
    vi.stubGlobal("fetch", request);
    render(<ConnectionStatus />);
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    expect(
      await screen.findByText("Connected. Your workspace is available."),
    ).toBeVisible();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it.each([
    { ok: false, body: ready },
    { ok: true, body: { status: "not_ready", service: "cgl-api", version: "0.1.0" } },
    { ok: true, body: { status: "ready", service: "another-service" } },
    { ok: true, body: { ...ready, scope: "database" } },
  ])(
    "does not report success for an unhealthy or invalid response: %j",
    async ({ ok, body }) => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok, json: async () => body }));
      render(<ConnectionStatus />);
      expect(await screen.findByRole("button", { name: "Try again" })).toBeEnabled();
    },
  );

  it("aborts the active request when the component unmounts", async () => {
    const request = vi.fn().mockImplementation(() => new Promise(() => {}));
    vi.stubGlobal("fetch", request);
    const { unmount } = render(<ConnectionStatus />);
    await waitFor(() => expect(request).toHaveBeenCalledOnce());
    const signal = request.mock.calls[0][1].signal as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
  });
});
