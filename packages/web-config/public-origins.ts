/** Public configuration only: cookie isolation depends on hostname, never port. */
export function publicOrigin(value: string | undefined, fallback: string): string {
  let url: URL;
  try {
    url = new URL(value ?? fallback);
  } catch {
    throw new Error("Configure a plain HTTP(S) public origin.");
  }
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error("Configure a plain HTTP(S) public origin.");
  }
  return url.origin;
}

export function publicOrigins(environment: Record<string, string | undefined>) {
  const appOrigin = publicOrigin(environment.APPLICATION_ORIGIN, "http://localhost:3000");
  const marketingOrigin = publicOrigin(
    environment.MARKETING_ORIGIN,
    "http://127.0.0.1:3001",
  );
  const hostname = (origin: string) => new URL(origin).hostname.replace(/\.+$/, "");
  if (hostname(appOrigin) === hostname(marketingOrigin)) {
    throw new Error("Application and marketing must use different cookie hostnames.");
  }
  return { appOrigin, marketingOrigin };
}
