import { NextRequest, NextResponse } from "next/server";
import { marketingOrigin } from "./lib/config";

export function proxy(request: NextRequest) {
  const allowed = new URL(marketingOrigin).host;
  if (request.headers.get("host") !== allowed)
    return new NextResponse("Not found", { status: 404 });
  if (
    request.nextUrl.pathname.startsWith("/api/") &&
    request.nextUrl.pathname !== "/api/inquiries"
  ) {
    return new NextResponse("Not found", { status: 404 });
  }
  return NextResponse.next();
}
export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
