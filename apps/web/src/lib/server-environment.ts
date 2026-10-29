export function readServerEnvironment(environment: Record<string, string | undefined>): {
  apiOrigin: string;
} {
  const configured = environment.API_INTERNAL_URL;

  if (!configured) {
    throw new Error("API_INTERNAL_URL is required. See apps/web/.env.example.");
  }

  let parsed: URL;
  try {
    parsed = new URL(configured);
  } catch {
    throw new Error("API_INTERNAL_URL must be an absolute HTTP(S) origin.");
  }

  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error(
      "API_INTERNAL_URL must be an HTTP(S) origin without credentials or a path.",
    );
  }

  return { apiOrigin: parsed.origin };
}
