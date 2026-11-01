import { NextRequest, NextResponse } from "next/server";
import { marketingOrigin, publicOrigin } from "../../../lib/config";

export async function POST(request: NextRequest) {
  if (request.headers.get("origin") !== marketingOrigin)
    return NextResponse.json({ error: "Origin rejected" }, { status: 403 });
  const size = Number(request.headers.get("content-length"));
  if (!Number.isInteger(size) || size <= 0 || size > 8192)
    return NextResponse.json({ error: "Bounded body required" }, { status: 413 });
  if (!request.headers.get("content-type")?.startsWith("application/json"))
    return NextResponse.json({ error: "JSON required" }, { status: 415 });
  if (!process.env.INQUIRY_INTERNAL_URL)
    return NextResponse.json({ error: "Inquiry service unavailable" }, { status: 503 });
  const reader = request.body?.getReader();
  if (!reader) return NextResponse.json({ error: "Body required" }, { status: 400 });
  let bytes = 0;
  const chunks: Uint8Array[] = [];
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    bytes += value.byteLength;
    if (bytes > 8192) {
      await reader.cancel();
      return NextResponse.json({ error: "Body too large" }, { status: 413 });
    }
    chunks.push(value);
  }
  const body = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    const upstream = publicOrigin(
      process.env.INQUIRY_INTERNAL_URL,
      "http://inquiries:8010",
    );
    const response = await fetch(upstream + "/inquiries", {
      method: "POST",
      body,
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": request.headers.get("idempotency-key") ?? "",
      },
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(10000),
    });
    const status = [200, 201, 400, 409, 422, 429].includes(response.status)
      ? response.status
      : 503;
    return NextResponse.json(
      status < 300 ? { received: true } : { error: "Inquiry could not be saved" },
      { status, headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json({ error: "Inquiry service unavailable" }, { status: 503 });
  }
}
