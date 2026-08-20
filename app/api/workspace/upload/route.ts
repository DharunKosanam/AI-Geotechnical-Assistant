import { NextRequest, NextResponse } from "next/server";

/**
 * Streaming proxy for GeoPilot session uploads (INSTRUMENT_PARSERS_ENABLED).
 *
 * Forwards POST /api/workspace/upload -> FastAPI POST /api/workspace/documents,
 * i.e. the SAME backend handler the plain document upload uses (it sniffs the
 * first 2 KB and takes the parser path on a match). It exists because
 * instrument files are 22-58 MB and the next.config rewrite that carries
 * /api/workspace/* truncates proxied bodies at 10 MB and times out at 30 s
 * (see app/api/upload/[[...path]]/route.ts for the full story). The body is
 * streamed straight through -- never buffered, never truncated -- with a long
 * explicit timeout. The frontend only uses this path when the backend reports
 * the instrument capability; with the flag off it keeps calling
 * /api/workspace/documents through the rewrite exactly as before.
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// Under nginx proxy_read_timeout (300s) so this route's clean error fires first.
const UPSTREAM_TIMEOUT_MS = 290_000;

export async function POST(request: NextRequest) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  const headers: Record<string, string> = {
    // Forward the httpOnly access_token cookie so FastAPI authenticates.
    cookie: request.headers.get("cookie") ?? "",
  };
  const auth = request.headers.get("authorization");
  if (auth) headers["authorization"] = auth;
  const ct = request.headers.get("content-type");
  if (ct) headers["content-type"] = ct; // preserve the multipart boundary

  const init: RequestInit & { duplex?: "half" } = {
    method: "POST",
    headers,
    signal: controller.signal,
    cache: "no-store",
    // Stream the file straight through -- never buffered, never truncated.
    body: request.body,
    duplex: "half",
  };

  try {
    const upstream = await fetch(`${PYTHON_API_URL}/api/workspace/documents`, init);
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
          ? "The upload took too long and timed out. Try again, or check your connection."
          : `Couldn't reach the server to upload this file: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
