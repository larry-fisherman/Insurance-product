export function publicOrigin(value: string | undefined, fallback: string): string {
  const url = new URL(value ?? fallback);
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
export const appOrigin = publicOrigin(
  process.env.APPLICATION_ORIGIN,
  "http://localhost:3000",
);
export const marketingOrigin = publicOrigin(
  process.env.MARKETING_ORIGIN,
  "http://127.0.0.1:3001",
);
