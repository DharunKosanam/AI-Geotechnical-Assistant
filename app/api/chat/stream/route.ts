import { NextRequest, NextResponse } from "next/server";

/**
 * Streaming proxy for the RAG endpoint — the SSE sibling of /api/chat.
 *
 * Differs from /api/chat in the two ways that matter:
 *
 *   1. It does NOT buffer. /api/chat does `await upstream.text()`, which would
 *      collapse the whole stream back into one blob and defeat the point; here
 *      the upstream body is handed to the client as-is.
 *   2. Its timeout bounds TIME TO FIRST BYTE, not the whole response. fetch()
 *      resolves as soon as FastAPI's headers arrive, so the deadline is cleared
 *      there and a long generation is free to keep streaming — which is the
 *      whole reason a slow answer no longer 504s. The backend heartbeats every
 *      15s, so a stalled connection still dies at the socket level.
 *
 * When STREAMING_ENABLED is off in FastAPI this route faithfully returns that
 * 404, which is the signal the chat client uses to fall back to /api/chat.
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// Only the wait for FastAPI's response headers, which precede any generation.
const FIRST_BYTE_TIMEOUT_MS = 30_000;

export async function POST(request: NextRequest) {
  const body = await request.text();

  const controller = new AbortController();
  let deadline: ReturnType<typeof setTimeout> | null = setTimeout(
    () => controller.abort(),
    FIRST_BYTE_TIMEOUT_MS,
  );
  const clearDeadline = () => {
    if (deadline !== null) {
      clearTimeout(deadline);
      deadline = null;
    }
  };

  try {
    const upstream = await fetch(`${PYTHON_API_URL}/chat/stream`, {
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

    // Headers are in. From here the clock belongs to the stream, not to us.
    clearDeadline();

    // Anything that is not a live stream (404 with the flag off, 401, 429, 500)
    // is a normal short body — pass it through unchanged so the client can read
    // `detail` and, for 404, fall back to /api/chat.
    if (!upstream.ok || !upstream.body) {
      const text = await upstream.text();
      return new NextResponse(text, {
        status: upstream.status,
        headers: {
          "Content-Type":
            upstream.headers.get("content-type") ?? "application/json",
        },
      });
    }

    return new NextResponse(upstream.body, {
      status: upstream.status,
      headers: {
        "Content-Type": upstream.headers.get("content-type") ?? "text/event-stream",
        // Repeat the anti-buffering headers on this hop: nginx sits in front of
        // Next, and it is nginx that would otherwise hold the stream back.
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
      },
    });
  } catch (err: any) {
    clearDeadline();
    const isTimeout = err?.name === "AbortError";
    return NextResponse.json(
      {
        detail: isTimeout
          ? "The assistant did not respond in time. Please try again."
          : `Failed to reach the backend: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  }
}
