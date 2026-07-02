import { NextRequest, NextResponse } from "next/server";

/**
 * Dedicated proxy for the slow RAG endpoint.
 *
 * The /chat call cold-loads the embedding + reranker models and then runs LLM
 * generation, so it can take a long time. Next.js rewrites() has a short,
 * non-configurable socket timeout that resets the connection ("socket hang up"
 * / ECONNRESET) and returns 500 even though FastAPI completes the request. This
 * Route Handler forwards to FastAPI with a long, explicit timeout instead.
 *
 * Only the exact POST /chat lives here. GET /chat/<id>/history stays on the
 * rewrites() proxy (it is fast).
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;          // let the route run up to 300s

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// 240s ceiling for the RAG response. Sits between the Ollama client timeout
// (180s, inside FastAPI) and nginx proxy_read_timeout (300s) so the chain fails
// inside-out and this route's clean 504 JSON fires before nginx's generic 504.
const UPSTREAM_TIMEOUT_MS = 240_000;

export async function POST(request: NextRequest) {
  // Read the JSON body once and forward it verbatim ({ query, history, threadId }).
  const body = await request.text();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${PYTHON_API_URL}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        // Forward the browser's cookies so the httpOnly access_token reaches
        // FastAPI and the request authenticates.
        cookie: request.headers.get("cookie") ?? "",
      },
      body,
      signal: controller.signal,
      cache: "no-store",
    });

    // Return FastAPI's response (body + status + content-type) unchanged.
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: {
        "Content-Type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (err: any) {
    const isTimeout = err?.name === "AbortError";
    // Match the backend's error shape ({ detail }) so the frontend surfaces it.
    return NextResponse.json(
      {
        detail: isTimeout
          ? "The assistant took too long to respond. Please try again."
          : `Failed to reach the backend: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
