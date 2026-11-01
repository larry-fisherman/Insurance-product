import { NextRequest, NextResponse } from "next/server";
export function proxy(request: NextRequest) {
  const origin = new URL(process.env.APPLICATION_ORIGIN ?? "http://localhost:3000");
  if (request.headers.get("host") !== origin.host)
    return new NextResponse("Unknown application host", { status: 421 });
  const headers = new Headers(request.headers);
  const nonce = btoa(crypto.randomUUID());
  const policy =
    "default-src 'self'; script-src 'self' 'nonce-" +
    nonce +
    "'" +
    (process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "") +
    "; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'";
  if (!request.nextUrl.pathname.startsWith("/api/")) {
    headers.set("x-nonce", nonce);
    headers.set("Content-Security-Policy", policy);
  }
  const response = NextResponse.next({ request: { headers } });
  if (!request.nextUrl.pathname.startsWith("/api/"))
    response.headers.set("Content-Security-Policy", policy);
  response.headers.set("Cache-Control", "no-store");
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
