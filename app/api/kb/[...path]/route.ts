import { NextRequest, NextResponse } from "next/server";

/**
 * Dedicated long-timeout proxy for /api/kb/*.
 *
 * The KB upload preflight (extract + first-embed + LLM metadata) and, above all,
 * a bulk submission run longer than the next.config rewrites() short, fixed
 * socket timeout — which resets the connection ("socket hang up" / ECONNRESET)
 * and surfaces as a 500 in the browser even though FastAPI completes (and logs
 * 200). This Route Handler forwards every /api/kb/* method to FastAPI with a
 * long, explicit timeout, STREAMING the (possibly large, multi-file) request
 * body so it is never buffered in the Node process. It replaces the /api/kb
 * rewrite. Mirrors app/api/chat/route.ts.
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// Under nginx proxy_read_timeout (300s) so this route's clean error fires first.
const UPSTREAM_TIMEOUT_MS = 290_000;

async function forward(
  request: NextRequest,
  path: string[],
  method: "GET" | "POST" | "DELETE",
) {
  const search = new URL(request.url).search;
  const target = `${PYTHON_API_URL}/api/kb/${(path || []).join("/")}${search}`;

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
    // Stream the (possibly large / many-file) body straight through — never buffer.
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
    return NextResponse.json(
      {
        detail: isTimeout
          ? "The upload took too long. It may still be processing — check your uploads list."
          : `Failed to reach the backend: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}

export async function GET(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path } = await ctx.params;
  return forward(request, path, "GET");
}

export async function POST(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path } = await ctx.params;
  return forward(request, path, "POST");
}

export async function DELETE(
  request: NextRequest,
  ctx: { params: Promise<{ path: string[] }> },
) {
  const { path } = await ctx.params;
  return forward(request, path, "DELETE");
}
