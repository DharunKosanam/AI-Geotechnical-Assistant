import { NextRequest, NextResponse } from "next/server";

/**
 * Streaming proxy for source-format generation — same contract as
 * /api/chat/stream: no buffering (the upstream SSE body is handed to the
 * client as-is) and the timeout bounds TIME TO FIRST BYTE only, so a
 * multi-minute generation is free to keep streaming; the backend heartbeats
 * every 15s. Non-stream statuses (404 flag-off, 409 busy, 400, 401, 429, 500)
 * pass through unchanged so the client can branch on them.
 */
export const dynamic = "force-dynamic";
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
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
    const upstream = await fetch(`${PYTHON_API_URL}/chat/formats/stream`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        cookie: request.headers.get("cookie") ?? "",
      },
      body,
      signal: controller.signal,
      cache: "no-store",
    });

    clearDeadline();

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
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        Connection: "keep-alive",
      },
    });
  } catch (error: unknown) {
    clearDeadline();
    const aborted = (error as Error | undefined)?.name === "AbortError";
    return NextResponse.json(
      {
        detail: aborted
          ? "The generation service took too long to start responding."
          : "Failed to reach the generation service.",
      },
      { status: aborted ? 504 : 502 },
    );
  }
}
