import { NextRequest, NextResponse } from "next/server";

/**
 * Dedicated proxy for the GeoPilot workspace chat endpoint.
 *
 * Like /api/chat and the CPT interpret proxy, a matched message runs the
 * deterministic calculator AND an LLM (Ollama) interpretation, so it can take a
 * while. Next.js rewrites() has a short socket timeout that resets long
 * requests, so we forward through this Route Handler with an explicit long
 * timeout instead. The JSON body is forwarded verbatim and the browser's
 * httpOnly auth cookie is passed through so FastAPI authenticates the request.
 */
export const dynamic = "force-dynamic"; // never cache; always hit FastAPI
export const maxDuration = 300;

const PYTHON_API_URL = process.env.PYTHON_API_URL || "http://localhost:8000";
// Mirror the chat proxy budget: below nginx (300s), above the Ollama client
// timeout inside FastAPI, so the chain fails inside-out.
const UPSTREAM_TIMEOUT_MS = 240_000;

export async function POST(request: NextRequest) {
  const body = await request.text();

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), UPSTREAM_TIMEOUT_MS);

  try {
    const upstream = await fetch(`${PYTHON_API_URL}/api/workspace/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
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
          ? "The request took too long. Please try again."
          : `Failed to reach the backend: ${err?.message ?? "unknown error"}`,
      },
      { status: isTimeout ? 504 : 502 },
    );
  } finally {
    clearTimeout(timer);
  }
}
