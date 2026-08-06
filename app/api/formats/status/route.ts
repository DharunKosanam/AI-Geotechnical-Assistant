import { NextRequest, NextResponse } from "next/server";

/**
 * Proxy for the source-formats feature handshake: forwards
 * GET /api/formats/status[?threadId=...] to FastAPI's /chat/formats/status.
 * The frontend fails closed (hides the buttons) on any non-OK response.
 */
export const dynamic = "force-dynamic";

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
const UPSTREAM_TIMEOUT_MS = 15_000;

export async function GET(request: NextRequest) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);
  try {
    const search = new URL(request.url).search;
    const upstream = await fetch(
      `${PYTHON_API_URL}/chat/formats/status${search}`,
      {
        headers: { cookie: request.headers.get("cookie") ?? "" },
        signal: controller.signal,
        cache: "no-store",
      },
    );
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch {
    return NextResponse.json({ enabled: false }, { status: 502 });
  } finally {
    clearTimeout(timer);
  }
}
