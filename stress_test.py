"""
WARNING: This hits the production API and uses Groq free tier credits. Use sparingly.
"""

import argparse
import asyncio
import io
import sys
import time

# Force UTF-8 stdout on Windows so emoji prints work
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import aiohttp

# The Python FastAPI backend URL (NOT the Next.js frontend).
# Override via --url flag or set here.
DEFAULT_BACKEND_URL = "https://ai-geotechnical-assistant-production.up.railway.app"

QUESTIONS = [
    "What is critical state soil mechanics?",
    "Compare CFD-DEM and CFD-MPM approaches for modeling erosion.",
    "What is EICP and how does it improve soil?",
    "Explain the key findings of Bolton 1986 on sand strength and dilatancy.",
    "What is the difference between drained and undrained shear strength?",
    "How does scour affect laterally loaded piles?",
    "What are the main erosion mechanisms in geomechanics according to Bonelli?",
    "Describe the Mohr-Coulomb failure criterion.",
    "What is the role of relative density in sand behavior?",
    "How is the factor of safety calculated for slope stability?",
]


async def send_question(
    session: aiohttp.ClientSession,
    question: str,
    index: int,
    base_url: str,
) -> dict:
    """Create a thread, send a question, and return timing/status info."""
    result = {
        "index": index,
        "question": question,
        "status": None,
        "time_s": 0.0,
        "sources": 0,
        "error": None,
        "rate_limited": False,
    }

    try:
        # 1. Create a new thread
        thread_url = f"{base_url}/api/assistants/threads"
        async with session.post(thread_url) as resp:
            if resp.status != 200:
                body_text = await resp.text()
                result["error"] = f"Thread creation failed ({resp.status})"
                print(f"  [!] Q{index+1}: Thread creation failed ({resp.status}): {body_text[:200]}")
                return result
            thread_data = await resp.json()

        thread_id = thread_data.get("threadId") or thread_data.get("id")
        if not thread_id:
            result["error"] = f"No threadId in response: {thread_data}"
            print(f"  [!] Q{index+1}: No threadId in response: {thread_data}")
            return result

        # 2. Send the question
        payload = {
            "query": question,
            "threadId": thread_id,
            "history": [],
        }

        chat_url = f"{base_url}/chat"
        t0 = time.perf_counter()
        async with session.post(chat_url, json=payload) as resp:
            elapsed = time.perf_counter() - t0
            result["time_s"] = round(elapsed, 2)
            result["status"] = resp.status

            if resp.status == 429:
                result["rate_limited"] = True
                result["error"] = "Rate limited (429)"
                print(f"  🚫 Q{index+1}: Rate limited after {result['time_s']}s")
                return result

            if resp.status == 200:
                body = await resp.json()
                sources = body.get("sources", [])
                result["sources"] = len(sources)
                answer_preview = (body.get("answer") or "")[:80]
                print(
                    f"  ✅ Q{index+1}: {result['time_s']}s | "
                    f"{result['sources']} sources | {answer_preview}…"
                )
            else:
                result["error"] = f"HTTP {resp.status}"
                print(f"  ❌ Q{index+1}: HTTP {resp.status} after {result['time_s']}s")

    except asyncio.TimeoutError:
        result["error"] = "Timeout"
        print(f"  ⏰ Q{index+1}: Timed out")
    except Exception as exc:
        result["error"] = str(exc)
        print(f"  💥 Q{index+1}: {exc}")

    return result


def print_summary(results: list[dict], label: str) -> None:
    total = len(results)
    successful = [r for r in results if r["status"] == 200]
    failed = [r for r in results if r["status"] != 200]
    rate_limited = [r for r in results if r["rate_limited"]]
    times = [r["time_s"] for r in successful]
    avg_time = sum(times) / len(times) if times else 0.0

    print("\n" + "=" * 60)
    print(f"📊  Summary — {label}")
    print("=" * 60)
    print(f"  Total requests :  {total}")
    print(f"  Successful     :  {len(successful)}")
    print(f"  Failed         :  {len(failed)}")
    print(f"  Rate limited   :  {len(rate_limited)}")
    print(f"  Avg response   :  {avg_time:.2f}s")
    if times:
        print(f"  Min / Max      :  {min(times):.2f}s / {max(times):.2f}s")
    print("=" * 60)


async def run_sequential() -> None:
    """Send 10 questions one after another with a 3-second gap."""
    print("\n🔄  Sequential test — 10 questions, 3s delay between each\n")
    timeout = aiohttp.ClientTimeout(total=120)
    results: list[dict] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for i, q in enumerate(QUESTIONS):
            print(f"  📨 Sending Q{i+1}: {q[:60]}…")
            result = await send_question(session, q, i)
            results.append(result)
            if i < len(QUESTIONS) - 1:
                await asyncio.sleep(3)

    print_summary(results, "Sequential (10 questions)")


async def run_concurrent(count: int, label: str) -> None:
    """Send `count` questions simultaneously."""
    subset = QUESTIONS[:count]
    print(f"\n⚡  Concurrent test ({label}) — {count} questions at once\n")
    timeout = aiohttp.ClientTimeout(total=120)
    results: list[dict] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [send_question(session, q, i) for i, q in enumerate(subset)]
        results = await asyncio.gather(*tasks)

    print_summary(list(results), f"Concurrent {label} ({count} questions)")


async def main(mode: str) -> None:
    print("⚠️  WARNING: This hits the production API and uses Groq free tier credits.")
    print(f"🎯  Mode: {mode}\n")

    if mode == "sequential":
        await run_sequential()
    elif mode == "light":
        await run_concurrent(3, "light")
    elif mode == "medium":
        await run_concurrent(5, "medium")
    elif mode == "all":
        await run_sequential()
        print("\n⏳  Pausing 5s before concurrent tests…\n")
        await asyncio.sleep(5)
        await run_concurrent(3, "light")
        print("\n⏳  Pausing 5s…\n")
        await asyncio.sleep(5)
        await run_concurrent(5, "medium")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stress-test the deployed Geotechnical AI assistant."
    )
    parser.add_argument(
        "--mode",
        choices=["sequential", "light", "medium", "all"],
        default="sequential",
        help="Test mode (default: sequential)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.mode))
