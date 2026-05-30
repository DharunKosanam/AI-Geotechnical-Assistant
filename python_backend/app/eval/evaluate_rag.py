"""
RAG evaluation harness — read-only against the existing pipeline.

Loads a JSON golden dataset, runs every question through the production
RAG flow (embedding -> vector search -> reranker -> Groq), then scores
each result with a Groq-hosted LLM-as-judge (llama-3.3-70b-versatile)
on faithfulness, answer_relevancy, context_precision, context_recall,
plus a simple retrieval hit-rate. Writes a timestamped report.

Run:
    python -m app.eval.evaluate_rag
    python -m app.eval.evaluate_rag --quick           # first 10 questions
    python -m app.eval.evaluate_rag --no-judge        # hit-rate only

The script imports query_vector_store and generate_answer_with_groq from
the existing services -- it does not duplicate retrieval logic. The
LLM-judge talks to Groq directly via httpx (no ragas / no langchain
adapters) to avoid the vertexai import chain that broke ragas.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table

# Reuse the production pipeline -- no duplication.
from app.services.rag_service import query_vector_store
from app.services.llm_service import generate_answer_with_groq
from app.core.config import GROQ_API_KEY, GROQ_MODEL

load_dotenv()
console = Console()

DATASET_PATH = Path(__file__).parent / "test_dataset.json"
RESULTS_DIR = Path(__file__).parent / "results"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class TestCase:
    question: str
    ground_truth: str
    id: str = ""
    source_documents: List[str] = field(default_factory=list)
    source_pages: List[int] = field(default_factory=list)
    difficulty: str = "factual"
    category: str = "knowledge_base"


@dataclass
class QuestionResult:
    question: str
    ground_truth: str
    difficulty: str
    expected_sources: List[str]
    answer: str
    retrieved_chunks: List[Dict[str, Any]]      # post-rerank chunks fed to LLM
    contexts: List[str]                          # chunk texts for RAGAS
    retrieved_sources: List[str]
    hit_rate: float
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------
def load_dataset(path: Path) -> List[TestCase]:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    # Support both flat array and nested {"questions": [...]} format
    if isinstance(raw, dict) and "questions" in raw:
        raw = raw["questions"]
    # Ignore unknown keys so future dataset versions don't crash the loader.
    allowed = {f.name for f in TestCase.__dataclass_fields__.values()}
    return [TestCase(**{k: v for k, v in entry.items() if k in allowed}) for entry in raw]


# ---------------------------------------------------------------------------
# Per-question pipeline run
# ---------------------------------------------------------------------------
async def run_question(tc: TestCase) -> QuestionResult:
    """Run a single test case through the existing RAG pipeline."""
    expected = {s.strip().lower() for s in tc.source_documents}

    # Retrieve + rerank (production path)
    chunks = await query_vector_store(tc.question, top_k=8)

    contexts = [c.get("text", "") for c in chunks]
    retrieved_sources = sorted({c.get("filename", "") for c in chunks if c.get("filename")})
    retrieved_norm = {s.lower() for s in retrieved_sources}

    if expected:
        hit = len(expected & retrieved_norm) / len(expected)
    else:
        # Unanswerable cases: "hit" = pipeline correctly retrieved nothing relevant.
        hit = 1.0 if not retrieved_norm else 0.0

    # Format the context the way chat.py does so the LLM sees the same prompt.
    formatted_context = "\n\n".join(
        f"[Source: {c.get('filename', 'unknown')}]\n{c.get('text', '')}"
        for c in chunks
    )

    answer = await generate_answer_with_groq(
        query=tc.question,
        context=formatted_context,
        history=None,
    )

    return QuestionResult(
        question=tc.question,
        ground_truth=tc.ground_truth,
        difficulty=tc.difficulty,
        expected_sources=tc.source_documents,
        answer=answer,
        retrieved_chunks=[
            {
                "filename": c.get("filename"),
                "page_start": c.get("pageStart"),
                "score": c.get("score"),
                "rerank_score": c.get("rerank_score"),
                "text_preview": (c.get("text") or "")[:200],
            }
            for c in chunks
        ],
        contexts=contexts,
        retrieved_sources=retrieved_sources,
        hit_rate=hit,
    )


# ---------------------------------------------------------------------------
# Custom LLM-as-judge (Groq direct, no ragas)
#
# Why custom: ragas pulls in a langchain_community import chain that fails
# on vertexai. Four small, prompt-driven scorers give us the same metrics
# without the dependency.
# ---------------------------------------------------------------------------
JUDGE_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
JUDGE_CALL_DELAY_S = 0.5         # spacing between calls to dodge TPM bursts
JUDGE_RATE_LIMIT_WAIT_S = 5.0    # back-off when Groq returns 429
JUDGE_RATE_LIMIT_RETRIES = 3
JUDGE_TIMEOUT_S = 60.0
CONTEXT_CHUNK_PREVIEW_CHARS = 500


_FAITHFULNESS_PROMPT = """You are an evaluation judge. Given the context chunks and the answer, \
score how faithful the answer is to the provided context.
Score 1.0 if every claim in the answer is supported by the context.
Score 0.0 if the answer contains claims not found in the context.
Score between 0 and 1 proportionally.

Context: {context}
Answer: {answer}

Respond with ONLY a JSON object: {{"score": 0.XX, "reason": "brief explanation"}}"""

_RELEVANCY_PROMPT = """You are an evaluation judge. Given the question and the answer, \
score how relevant the answer is to the question asked.
Score 1.0 if the answer directly and completely addresses the question.
Score 0.0 if the answer is completely off-topic.

Question: {question}
Answer: {answer}

Respond with ONLY a JSON object: {{"score": 0.XX, "reason": "brief explanation"}}"""

_PRECISION_PROMPT = """You are an evaluation judge. Given the question and the retrieved \
context chunks (in ranked order), score whether the most relevant chunks appear first.
Score 1.0 if the most relevant chunks are at the top.
Score 0.0 if relevant chunks are buried at the bottom or missing.

Question: {question}
Context chunks (ranked):
{ranked_chunks}

Respond with ONLY a JSON object: {{"score": 0.XX, "reason": "brief explanation"}}"""

_RECALL_PROMPT = """You are an evaluation judge. Given the ground truth answer and \
the retrieved context chunks, score whether the context contains enough information \
to derive the ground truth answer.
Score 1.0 if all information in the ground truth can be found in the context.
Score 0.0 if the context contains none of the needed information.

Ground truth: {ground_truth}
Context: {context}

Respond with ONLY a JSON object: {{"score": 0.XX, "reason": "brief explanation"}}"""


def _format_ranked_chunks(contexts: List[str], n: int = 5) -> str:
    """Top-n chunks as a numbered list, each truncated to keep the prompt small."""
    lines = []
    for i, c in enumerate(contexts[:n], 1):
        snippet = (c or "")[:CONTEXT_CHUNK_PREVIEW_CHARS]
        lines.append(f"{i}: {snippet}")
    return "\n".join(lines) if lines else "(no chunks retrieved)"


def _parse_score(text: str) -> Optional[float]:
    """Extract a numeric score from the judge response. Returns None on failure."""
    if not text:
        return None
    # Find the first {...} block -- the judge sometimes prefixes with whitespace
    # or a fenced code block despite the "ONLY JSON" instruction.
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if not match:
        return None
    try:
        obj = json.loads(match.group(0))
        score = obj.get("score")
        if score is None:
            return None
        score = float(score)
        # Clamp to [0, 1] in case the judge returns 1.5 / -0.2 etc.
        return max(0.0, min(1.0, score))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def _groq_chat(
    client: httpx.AsyncClient,
    prompt: str,
    model: str,
    api_key: str,
) -> str:
    """
    One Groq chat completion with 429-aware retry.
    Returns the assistant message text, or "" if all retries fail.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 256,
    }
    for attempt in range(JUDGE_RATE_LIMIT_RETRIES):
        try:
            resp = await client.post(GROQ_URL, headers=headers, json=body, timeout=JUDGE_TIMEOUT_S)
            if resp.status_code == 429:
                await asyncio.sleep(JUDGE_RATE_LIMIT_WAIT_S)
                continue
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"] or ""
        except httpx.HTTPStatusError as e:
            # Non-429 HTTP error -- don't retry, surface empty so caller defaults to None.
            console.print(f"[yellow]Judge HTTP {e.response.status_code}: {e.response.text[:200]}[/yellow]")
            return ""
        except (httpx.RequestError, asyncio.TimeoutError) as e:
            # Transient network error -- one more shot, then give up.
            if attempt == JUDGE_RATE_LIMIT_RETRIES - 1:
                console.print(f"[yellow]Judge request failed: {e}[/yellow]")
                return ""
            await asyncio.sleep(JUDGE_RATE_LIMIT_WAIT_S)
    return ""


async def _score_metric(
    client: httpx.AsyncClient,
    prompt: str,
    api_key: str,
) -> Optional[float]:
    """One scored prompt with one parse-failure retry."""
    raw = await _groq_chat(client, prompt, JUDGE_MODEL, api_key)
    score = _parse_score(raw)
    if score is not None:
        return score
    # Retry once on parse failure -- the judge sometimes returns prose first.
    await asyncio.sleep(JUDGE_CALL_DELAY_S)
    raw = await _groq_chat(client, prompt, JUDGE_MODEL, api_key)
    return _parse_score(raw)


async def score_with_llm_judge(
    results: List[QuestionResult],
    judge_model: str = JUDGE_MODEL,
) -> None:
    """
    Score every successful result on faithfulness / answer_relevancy /
    context_precision / context_recall using Groq direct API calls.

    For unanswerable questions, faithfulness and context_recall are skipped
    (set to None) because there is no meaningful reference to ground against.
    """
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY not set -- cannot run LLM judge")

    successful = [r for r in results if r.error is None]
    if not successful:
        console.print("[yellow]No successful rows to score.[/yellow]")
        return

    console.print(f"[cyan]Running LLM judge ({judge_model}) over {len(successful)} result(s)...[/cyan]")

    async with httpx.AsyncClient() as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Judging", total=len(successful))
            for r in successful:
                ctx_joined = "\n".join(r.contexts) if r.contexts else "(no context)"
                ranked = _format_ranked_chunks(r.contexts)
                is_unanswerable = (r.difficulty or "").lower() == "unanswerable"

                # 4 calls per question, spaced by JUDGE_CALL_DELAY_S.
                if is_unanswerable:
                    r.metrics["faithfulness"] = None
                else:
                    r.metrics["faithfulness"] = await _score_metric(
                        client,
                        _FAITHFULNESS_PROMPT.format(context=ctx_joined, answer=r.answer),
                        GROQ_API_KEY,
                    )
                    await asyncio.sleep(JUDGE_CALL_DELAY_S)

                r.metrics["answer_relevancy"] = await _score_metric(
                    client,
                    _RELEVANCY_PROMPT.format(question=r.question, answer=r.answer),
                    GROQ_API_KEY,
                )
                await asyncio.sleep(JUDGE_CALL_DELAY_S)

                r.metrics["context_precision"] = await _score_metric(
                    client,
                    _PRECISION_PROMPT.format(question=r.question, ranked_chunks=ranked),
                    GROQ_API_KEY,
                )
                await asyncio.sleep(JUDGE_CALL_DELAY_S)

                if is_unanswerable:
                    r.metrics["context_recall"] = None
                else:
                    r.metrics["context_recall"] = await _score_metric(
                        client,
                        _RECALL_PROMPT.format(ground_truth=r.ground_truth, context=ctx_joined),
                        GROQ_API_KEY,
                    )
                    await asyncio.sleep(JUDGE_CALL_DELAY_S)

                progress.update(task, advance=1)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _avg(vals: List[Optional[float]]) -> Optional[float]:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def print_summary(results: List[QuestionResult]) -> None:
    table = Table(title="RAG Evaluation Results", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Question", overflow="fold", max_width=40)
    table.add_column("Diff", width=11)
    table.add_column("Hit", justify="right")
    table.add_column("Faith", justify="right")
    table.add_column("Rel", justify="right")
    table.add_column("Prec", justify="right")
    table.add_column("Rec", justify="right")

    def fmt(v: Optional[float]) -> str:
        return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

    for i, r in enumerate(results, 1):
        if r.error:
            table.add_row(str(i), r.question[:60], r.difficulty, "[red]ERR[/red]", "-", "-", "-", "-")
            continue
        m = r.metrics
        table.add_row(
            str(i),
            r.question[:60],
            r.difficulty,
            fmt(r.hit_rate),
            fmt(m.get("faithfulness")),
            fmt(m.get("answer_relevancy")),
            fmt(m.get("context_precision")),
            fmt(m.get("context_recall")),
        )

    avgs = {
        "hit_rate": _avg([r.hit_rate for r in results if not r.error]),
        "faithfulness": _avg([r.metrics.get("faithfulness") for r in results if not r.error]),
        "answer_relevancy": _avg([r.metrics.get("answer_relevancy") for r in results if not r.error]),
        "context_precision": _avg([r.metrics.get("context_precision") for r in results if not r.error]),
        "context_recall": _avg([r.metrics.get("context_recall") for r in results if not r.error]),
    }
    table.add_section()
    table.add_row(
        "[bold]Avg[/bold]", "", "",
        fmt(avgs["hit_rate"]),
        fmt(avgs["faithfulness"]),
        fmt(avgs["answer_relevancy"]),
        fmt(avgs["context_precision"]),
        fmt(avgs["context_recall"]),
    )

    console.print(table)
    failures = [r for r in results if r.error]
    if failures:
        console.print(f"\n[red]{len(failures)} question(s) failed:[/red]")
        for r in failures:
            console.print(f"  - {r.question[:80]} -> {r.error}")


def save_report(results: List[QuestionResult], judge_model: str, quick: bool) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = "_quick" if quick else ""
    path = RESULTS_DIR / f"eval_{ts}{suffix}.json"

    payload = {
        "timestamp": ts,
        "judge_model": judge_model,
        "model": GROQ_MODEL,
        "quick": quick,
        "count": len(results),
        "averages": {
            "hit_rate": _avg([r.hit_rate for r in results if not r.error]),
            "faithfulness": _avg([r.metrics.get("faithfulness") for r in results if not r.error]),
            "answer_relevancy": _avg([r.metrics.get("answer_relevancy") for r in results if not r.error]),
            "context_precision": _avg([r.metrics.get("context_precision") for r in results if not r.error]),
            "context_recall": _avg([r.metrics.get("context_recall") for r in results if not r.error]),
        },
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
async def run_all(cases: List[TestCase]) -> List[QuestionResult]:
    results: List[QuestionResult] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Running questions", total=len(cases))
        for tc in cases:
            try:
                r = await run_question(tc)
            except Exception as e:
                tb = traceback.format_exc(limit=2)
                r = QuestionResult(
                    question=tc.question,
                    ground_truth=tc.ground_truth,
                    difficulty=tc.difficulty,
                    expected_sources=tc.source_documents,
                    answer="",
                    retrieved_chunks=[],
                    contexts=[],
                    retrieved_sources=[],
                    hit_rate=0.0,
                    error=f"{e.__class__.__name__}: {e}\n{tb}",
                )
            results.append(r)
            progress.update(task, advance=1)
    return results


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate the geotechnical RAG pipeline.")
    p.add_argument("--quick", action="store_true", help="Only run the first 10 questions.")
    p.add_argument("--dataset", type=Path, default=DATASET_PATH, help="Path to test dataset JSON.")
    p.add_argument(
        "--judge-model",
        default=JUDGE_MODEL,
        help=f"Groq model name used as the LLM judge (default: {JUDGE_MODEL}).",
    )
    # --no-judge is the canonical flag. --no-ragas kept as a hidden alias so
    # older invocations / docs keep working.
    p.add_argument(
        "--no-judge", "--no-ragas",
        dest="no_judge",
        action="store_true",
        help="Skip the LLM judge entirely (hit-rate only).",
    )
    return p.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    if not args.dataset.exists():
        console.print(f"[red]Dataset not found: {args.dataset}[/red]")
        return 1

    cases = load_dataset(args.dataset)
    if args.quick:
        cases = cases[:10]
    console.print(f"[cyan]Loaded {len(cases)} test case(s) from {args.dataset.name}[/cyan]")

    results = await run_all(cases)

    if not args.no_judge:
        try:
            await score_with_llm_judge(results, judge_model=args.judge_model)
        except Exception as e:
            console.print(f"[red]LLM judge failed:[/red] {e.__class__.__name__}: {e}")
            console.print("[dim]" + traceback.format_exc() + "[/dim]")
            console.print("[yellow]Continuing with hit-rate only.[/yellow]")

    print_summary(results)
    out = save_report(results, args.judge_model, args.quick)
    console.print(f"\n[green]Report saved -> {out}[/green]")
    return 0


def main() -> None:
    args = parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
