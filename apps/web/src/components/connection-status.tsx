"use client";

import { useCallback, useEffect, useState } from "react";

import { isReadyResponse } from "../lib/readiness";

type Connection = "checking" | "ready" | "unavailable";

export function ConnectionStatus() {
  const [connection, setConnection] = useState<Connection>("checking");
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => {
    setConnection("checking");
    setAttempt((current) => current + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => controller.abort(), 5000);

    async function checkConnection() {
      try {
        const response = await fetch("/api/v1/health/ready", {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok || !isReadyResponse(await response.json())) {
          throw new Error("Service is not ready");
        }
        if (active) setConnection("ready");
      } catch {
        if (active) setConnection("unavailable");
      } finally {
        window.clearTimeout(timeout);
      }
    }

    void checkConnection();
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [attempt]);

  return (
    <section className="connection-card" aria-labelledby="connection-heading">
      <div className="connection-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none">
          <path d="M5 12h3l3-7 3 14 3-7h2" stroke="currentColor" strokeWidth="1.7" />
        </svg>
      </div>
      <div className="connection-copy">
        <h2 id="connection-heading">Workspace connection</h2>
        <p role="status" aria-live="polite">
          {connection === "checking" && "Checking your connection…"}
          {connection === "ready" && "Connected. Your workspace is available."}
          {connection === "unavailable" && "Unable to connect. Please try again."}
        </p>
      </div>
      <div className="connection-actions">
        <span className={`connection-state ${connection}`}>
          <span className="status-dot" aria-hidden="true" />
          {connection === "checking"
            ? "Checking"
            : connection === "ready"
              ? "Connected"
              : "Offline"}
        </span>
        <button type="button" onClick={retry} disabled={connection === "checking"}>
          {connection === "unavailable" ? "Try again" : "Refresh"}
        </button>
      </div>
    </section>
  );
}
