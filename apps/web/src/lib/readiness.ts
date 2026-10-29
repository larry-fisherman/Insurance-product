import type { components } from "@cgl/contracts";

export type Readiness = components["schemas"]["ReadinessResponse"];

export function isReadyResponse(value: unknown): value is Readiness {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    candidate.status === "ready" &&
    candidate.service === "cgl-api" &&
    candidate.scope === "application" &&
    typeof candidate.version === "string" &&
    candidate.version.length > 0
  );
}
