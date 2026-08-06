import { NextRequest, NextResponse } from "next/server";

/**
 * Dedicated long-timeout proxy for /api/upload and /api/upload/* (the chat
 * thread attachment path behind the composer's "+" button).
 *
 * This REPLACES the next.config rewrite these paths used to ride, which broke
 * thread uploads in two ways that never showed up on small test files:
 *
 *   1. Body cap. A rewrite-proxied request is passed through Next's clonable
 *      body (resolve-routes -> getCloneableBody), which TRUNCATES at
 *      experimental.proxyClientMaxBodySize — 10 MB by default. Past that Next
 *      ends the upstream stream early while FastAPI is still waiting for the
 *      declared Content-Length, so the socket hangs until the rewrite's
 *      proxyTimeout (30 s default) fires and the browser gets a plain-text
 *      "500 Internal Server Error". The request never reaches FastAPI at all.
 *      The composer accepts files up to 50 MB, so every attachment over 10 MB
 *      failed — deterministically, regardless of network.
 *   2. Timeout. That same 30 s proxyTimeout also killed any upload whose body
 *      stalled for 30 s (a flaky VPN mid-transfer), again as a 500.
 *
 * Streaming the body through a Route Handler avoids the clone entirely (no
 * cap, nothing buffered in the Node heap) and sets an explicit long timeout.
 * Mirrors app/api/kb/[...path]/route.ts and app/api/chat/route.ts.
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// Under nginx proxy_read_timeout (300s) so this route's clean error fires first.
const UPSTREAM_TIMEOUT_MS = 290_000;

async function forward(
  request: NextRequest,
  path: string[] | undefined,
  method: "GET" | "POST" | "DELETE",
) {
  const search = new URL(request.url).search;
  const suffix = (path || []).join("/");
  const target = `${PYTHON_API_URL}/api/upload${suffix ? `/${suffix}` : ""}${search}`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  const headers: Record<string, string> = {
    // Forward the httpOnly access_token cookie so FastAPI authenticates.
    cookie: request.headers.get("cookie") ?? "",
  };
  const auth = request.headers.get("authorization");
  if (auth) headers["authorization"] = auth; // also support Bearer clients
  const ct = request.headers.get("content-type");
  if (ct) headers["content-type"] = ct; // preserve the multipart boundary

  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers,
    signal: controller.signal,
    cache: "no-store",
  };
  if (method === "POST") {
    // Stream the file straight through — never buffered, never truncated.
    init.body = request.body;
    init.duplex = "half";
  }

  try {
    const upstream = await fetch(target, init);
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (err: any) {
    const isTimeout = err?.name === "AbortError";
    // Always JSON with a `detail` — the composer reads errorData.detail, and a
    // plain-text body there is what produced the old useless "Unknown error".
    return NextResponse.json(
      {
        detail: isTimeout
          ? "The upload took too long and timed out. Try a smaller file, or check your connection."
          : `Couldn't reach the server to upload this file: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(
  request: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await ctx.params;
  return forward(request, path, "GET");
}

export async function POST(
  request: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await ctx.params;
  return forward(request, path, "POST");
}

export async function DELETE(
  request: NextRequest,
  ctx: { params: Promise<{ path?: string[] }> },
) {
  const { path } = await ctx.params;
  return forward(request, path, "DELETE");
}
