import { NextRequest, NextResponse } from "next/server";

/**
 * Dedicated proxy for the GeoPilot CPT interpret endpoint.
 *
 * Like /api/chat, this call runs an LLM (Ollama) after the deterministic calc,
 * so it can take a while. Next.js rewrites() has a short socket timeout that
 * resets long requests, so we forward through this Route Handler with an
 * explicit long timeout instead. The multipart body (the uploaded .CPT file +
 * optional form fields) is forwarded verbatim, and the browser's httpOnly auth
 * cookie is passed through so FastAPI authenticates the request.
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// Mirror the chat proxy budget: below nginx (300s), above the Ollama client
// timeout inside FastAPI, so the chain fails inside-out.
const UPSTREAM_TIMEOUT_MS = 240_000;

export async function POST(request: NextRequest) {
  // Forward the raw multipart body untouched so the boundary stays intact.
  const body = await request.arrayBuffer();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${PYTHON_API_URL}/api/workspace/cpt/interpret`, {
      method: "POST",
      headers: {
        // Preserve the multipart content-type (including the boundary param).
        "Content-Type":
          request.headers.get("content-type") ?? "application/octet-stream",
        cookie: request.headers.get("cookie") ?? "",
      },
      body,
      signal: controller.signal,
      cache: "no-store",
    });

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
    return NextResponse.json(
      {
        detail: isTimeout
          ? "Interpretation took too long. Please try again."
          : `Failed to reach the backend: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
