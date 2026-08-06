"""Engineering Workspace API routes (GeoPilot CPT lane).

Thin HTTP layer over the existing workspace modules: the route only parses the
upload, calls parser -> calculator -> layer_summary -> interpret_sounding, and
serializes the result. All engineering logic stays in those modules.

Every route uses the SAME auth dependency as chat (``get_current_user``) and is
gated by ``WORKSPACE_ENABLED``: when the flag is off the interpret endpoint
returns 404 (feature does not exist) and the status endpoint reports
``enabled: false`` so the frontend hides the GeoPilot toggle. The live chatbot
is untouched -- this router is purely additive.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import Response
from pydantic import BaseModel

from app.core import config
from app.dependencies.auth import get_current_user
from app.workspace import export, history_store, store
from app.workspace.calculators import registry
from app.workspace.calculators.cpt_interpretation import interpret_cpt
from app.workspace.interpretation.ai_interpret import interpret_sounding
from app.workspace.interpretation.layer_summary import Stratigraphy, summarize
from app.workspace.interpretation.qa import answer_question
from app.workspace.router import route_message
from app.workspace.parsers.cpt import parse_cpt_text
from models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# Hard ceiling for an uploaded sounding. CPT text files are tiny (a few hundred
# rows), so this is generous while still rejecting a stray large upload.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB


def require_workspace_enabled() -> None:
    """Gate: 404 (as if the route does not exist) when the flag is off.

    Read via the config module at call time so tests can toggle the flag with
    monkeypatch and so a deployment env change takes effect on reload.
    """
    if not config.WORKSPACE_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not found"
        )


def _layers_json(strat: Stratigraphy) -> list[dict]:
    """Serialize the detected layers for the results table."""
    return [
        {
            "depth_from": ly.depth_from,
            "depth_to": ly.depth_to,
            "thickness": ly.thickness,
            "sbt_zone": ly.sbt_zone,
            "soil_type": ly.dominant_sbt,
            "qc_mean": ly.qc_mean,
            "qc_min": ly.qc_min,
            "qc_max": ly.qc_max,
            "ic_mean": ly.ic_mean,
            "ic_min": ly.ic_min,
            "ic_max": ly.ic_max,
            "bq_mean": ly.bq_mean,
            "n_rows": ly.n_rows,
            "notes": ly.notes,
        }
        for ly in strat.layers
    ]


@router.get("/status")
async def workspace_status(current_user: User = Depends(get_current_user)) -> dict:
    """Report whether the workspace feature is enabled (auth required).

    Always 200 so the authenticated frontend can decide whether to show the
    GeoPilot toggle. NOT gated -- reporting ``enabled: false`` is the whole
    point when the flag is off.
    """
    return {"enabled": bool(config.WORKSPACE_ENABLED)}


@router.post("/cpt/interpret", dependencies=[Depends(require_workspace_enabled)])
async def interpret_cpt_upload(
    file: UploadFile = File(...),
    groundwater_level: Optional[float] = Form(None),
    soil_unit_weight: Optional[float] = Form(None),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Parse an uploaded .CPT sounding and return layers + AI interpretation.

    area_ratio comes from the file header (``MA``/``area_ratio``); GWL and unit
    weight default from the sounding but may be overridden by the optional form
    fields.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty"
        )
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    try:
        text = raw.decode("utf-8", errors="replace")
        sounding = parse_cpt_text(text, source_name=file.filename or "upload.cpt")
    except ValueError as exc:
        # Malformed sounding -> 422 with the parser's explanation.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        )

    results = interpret_cpt(
        sounding, gwl=groundwater_level, unit_weight=soil_unit_weight
    )
    strat = summarize(results)
    interp = await interpret_sounding(results)

    return {
        "layers": _layers_json(strat),
        "interpretation": {
            "narrative": interp.narrative,
            "per_layer_notes": interp.per_layer_notes,
            "flagged_concerns": interp.flagged_concerns,
            "is_ai_draft": interp.is_ai_draft,
            "model": interp.model,
        },
        "header": {
            **sounding.header,
            "max_depth": results.max_depth,
            "gwl": results.gwl,
            "area_ratio": results.area_ratio,
            "area_ratio_source": results.area_ratio_source,
            "n_layers": strat.n_layers,
        },
    }


# ---------------------------------------------------------------------------
# Session documents (the pool the calculators draw from)
# ---------------------------------------------------------------------------
# GeoPilot documents are per-user session data read verbatim by the
# deterministic calculators -- NOT knowledge-base material. They are held in the
# in-memory session store, never the RAG / vector pipeline. All three routes are
# gated by WORKSPACE_ENABLED and scoped to the authenticated user.


@router.post(
    "/documents",
    dependencies=[Depends(require_workspace_enabled)],
    status_code=status.HTTP_201_CREATED,
)
async def upload_workspace_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Store an uploaded document in the current GeoPilot session pool.

    The file body is decoded to text and kept in the session store; the
    calculator that needs it parses it at run time. Returns the compact
    ``{id, filename, extension, status}`` record the frontend tracks.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="File is empty"
        )
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    text = raw.decode("utf-8", errors="replace")
    doc = store.add_document(
        current_user.id, filename=file.filename or "upload.cpt", text=text
    )
    return doc.public()


@router.get("/documents", dependencies=[Depends(require_workspace_enabled)])
async def list_workspace_documents(
    current_user: User = Depends(get_current_user),
) -> dict:
    """List the current user's session documents (most-recent first)."""
    docs = store.list_documents(current_user.id)
    return {"documents": [d.public() for d in docs]}


@router.delete(
    "/documents/{doc_id}", dependencies=[Depends(require_workspace_enabled)]
)
async def delete_workspace_document(
    doc_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Remove a document from the session pool (idempotent)."""
    removed = store.remove_document(current_user.id, doc_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    return {"deleted": doc_id}


# ---------------------------------------------------------------------------
# Chat routing: message -> deterministic test (explicit intent matching only)
# ---------------------------------------------------------------------------


class ChatMessageIn(BaseModel):
    """A single GeoPilot chat message from the user.

    ``thread_id`` is the client's active History thread (None for a fresh
    session); the reply echoes back the thread id the message was recorded in.
    """

    message: str
    thread_id: Optional[str] = None


@router.post("/chat", dependencies=[Depends(require_workspace_enabled)])
async def workspace_chat(
    body: ChatMessageIn,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Route a chat message to a calculator via EXPLICIT trigger matching.

    Behaviour (never guesses, never fabricates inputs):
      * no trigger matches      -> list the available tests + trigger phrases;
      * trigger matches, doc missing -> ask the user to upload the required doc;
      * trigger matches, doc present -> run the deterministic calculator, then
        the AI interpretation as a separate labelled section, and return both.
    """
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Message is empty"
        )

    # Route to a calculator by INTENT: a fast exact-phrase pre-check first, then
    # the LLM router. The LLM only selects which calculator to run (or null); it
    # never computes anything.
    calc = await route_message(message)

    # Build the reply and the assistant message to record. ``run_title`` seeds a
    # new thread's title when this message is a fresh run.
    if calc is None:
        reply, assistant_msg = await _handle_non_command(message, current_user.id)
        run_title = None
    else:
        reply, assistant_msg, run_title = await _handle_calculator(
            message, calc, current_user.id
        )

    # Persist: append the user message + the assistant reply to the active
    # thread (creating one if needed). Best-effort — a persistence hiccup must
    # never break the chat reply the user is waiting on.
    thread_id = await _record_turn(
        current_user.id, body.thread_id, message, assistant_msg, run_title
    )
    reply["thread_id"] = thread_id
    return reply


async def _handle_non_command(message: str, user_id: str) -> tuple[dict, dict]:
    """No calculator selected: scoped Q&A if a result exists, else list tests."""
    latest = store.latest_result(user_id)
    if latest is not None and latest.get("calculator_id") == "cpt_interpretation":
        try:
            qa = await answer_question(message, latest)
        except Exception as exc:  # noqa: BLE001 - surface as a chat message
            reply = {"type": "error", "text": f"Could not answer that question: {exc}"}
            return reply, {"role": "assistant", "type": "error", "content": reply["text"]}
        reply = {
            "type": "answer",
            "answer": qa["answer"],
            "is_ai_draft": qa.get("is_ai_draft", True),
            "model": qa.get("model"),
            "source_file": latest.get("source_file"),
        }
        return reply, {
            "role": "assistant",
            "type": "answer",
            "content": qa["answer"],
            "is_ai_draft": qa.get("is_ai_draft", True),
        }

    reply = {"type": "info", "text": registry.available_tests_text()}
    return reply, {"role": "assistant", "type": "info", "content": reply["text"]}


async def _handle_calculator(
    message: str, calc, user_id: str
) -> tuple[dict, dict, Optional[str]]:
    """A calculator was selected: check the doc, compute, interpret, persist run."""
    doc = store.latest_document_with_extension(user_id, calc.required_extension)
    if doc is None:
        reply = {
            "type": "need_upload",
            "calculator_id": calc.id,
            "text": (
                f"To run {calc.name}, upload a {calc.required_label} into the "
                f"Documents panel first, then send your message again."
            ),
        }
        return reply, {"role": "assistant", "type": "need_upload", "content": reply["text"]}, None

    params = registry.parse_params(message, calc)

    # Deterministic compute. A present-but-unparseable document is reported as a
    # graceful assistant error, not a crash — inputs are never fabricated.
    try:
        result = calc.compute(doc.text, doc.filename, params)
    except ValueError as exc:
        reply = {
            "type": "error",
            "calculator_id": calc.id,
            "source_file": doc.filename,
            "text": f"Could not run {calc.name} on {doc.filename}: {exc}",
        }
        return reply, {"role": "assistant", "type": "error", "content": reply["text"]}, None

    # AI interpretation is a separate, clearly-labelled section. If it fails
    # (e.g. the model is unreachable) the deterministic result is still returned.
    interpretation = None
    if calc.interpret is not None:
        try:
            interpretation = await calc.interpret(result.raw)
        except Exception as exc:  # noqa: BLE001 - never lose the deterministic result
            interpretation = {
                "narrative": "",
                "per_layer_notes": [],
                "flagged_concerns": [],
                "is_ai_draft": True,
                "error": f"AI interpretation unavailable: {exc}",
            }

    # Stash the deterministic output in-memory so scoped Q&A can reference it.
    result_id = store.store_result(
        user_id,
        {
            "calculator_id": calc.id,
            "calculator_name": calc.name,
            "source_file": doc.filename,
            "reference": calc.reference,
            "layers": result.layers,
            "metadata": result.metadata,
            "flagged_concerns": (interpretation or {}).get("flagged_concerns", []),
        },
    )

    # Durable run record: the DETERMINISTIC result object (used for the generic
    # export) plus a compact summary. The Excel export reads this from Mongo so
    # it survives a restart. Best-effort: if persistence fails, the run is still
    # returned but is not exportable.
    export_payload = {
        "calculator_id": calc.id,
        "calculator_name": calc.name,
        "source_file": doc.filename,
        "reference": calc.reference,
        "layers": result.layers,
        "metadata": result.metadata,
        # Standard export schema -> the generic workbook builder.
        "tables": result.tables,
        "summary": result.summary,
    }
    run_summary = {
        "layer_count": len(result.layers),
        "max_depth": result.metadata.get("max_depth"),
        "gwl": result.metadata.get("groundwater_level"),
        "area_ratio": result.metadata.get("area_ratio"),
        "reference": calc.reference,
    }
    run_id = None
    try:
        run_id = await history_store.create_run(
            user_id, calc.id, doc.filename, export_payload, run_summary
        )
    except Exception:  # noqa: BLE001 - persistence must not break the reply
        logger.exception("Failed to persist workspace run")

    reply = {
        "type": "result",
        "calculator_id": calc.id,
        "calculator_name": calc.name,
        "source_file": doc.filename,
        "reference": calc.reference,
        "params": params,
        "summary_text": result.summary_text,
        "layers": result.layers,
        "metadata": result.metadata,
        "interpretation": interpretation,
        "result_id": result_id,
        "run_id": run_id,
        # Export button shows only when the run is persisted AND declares a table.
        "exportable": run_id is not None and bool(result.tables),
    }
    # The recorded assistant message carries the full reply (incl. interpretation)
    # so re-opening the thread re-renders the result card exactly.
    assistant_msg = {"role": "assistant", "type": "result", "content": reply}
    run_title = f"{doc.filename} - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
    return reply, assistant_msg, run_title


async def _record_turn(
    user_id: str,
    thread_id: Optional[str],
    message: str,
    assistant_msg: dict,
    run_title: Optional[str],
) -> Optional[str]:
    """Append the user + assistant messages to the active thread (create if none).

    Returns the thread id the turn was recorded in, or None if persistence was
    unavailable (the chat reply is returned regardless).
    """
    try:
        active = thread_id if thread_id and await history_store.thread_exists(
            user_id, thread_id
        ) else None
        if active is None:
            title = run_title or (
                "GeoPilot session - "
                + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            )
            active = await history_store.create_thread(user_id, title)
        await history_store.append_message(
            user_id, active, {"role": "user", "type": "text", "content": message}
        )
        await history_store.append_message(user_id, active, assistant_msg)
        return active
    except Exception:  # noqa: BLE001 - persistence must not break the reply
        logger.exception("Failed to persist workspace thread turn")
        return thread_id


# ---------------------------------------------------------------------------
# Excel export of a deterministic result
# ---------------------------------------------------------------------------
_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


@router.get(
    "/export/{run_id}", dependencies=[Depends(require_workspace_enabled)]
)
async def export_result_xlsx(
    run_id: str,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Stream a persisted run's deterministic result as an .xlsx download.

    Fetches the run by id AND user_id from ``workspace_runs`` and rebuilds the
    workbook GENERICALLY from the stored ``result_object`` tables + summary (the
    SAME structured object the calculator returned) -- so the link keeps working
    after a backend restart. Never reads the AI text or the raw file. A
    missing/foreign id returns a clean 404, not a 500.
    """
    run = await history_store.get_run(current_user.id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result expired or not found - re-run the test.",
        )
    payload = run.get("result_object", {})
    if not export.result_has_tables(payload):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This result has no exportable table.",
        )

    xlsx_bytes = export.build_workbook(payload)
    filename = export.export_filename(payload.get("source_file", "sounding"))
    return Response(
        content=xlsx_bytes,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# History: per-user runs + threads (durable, scoped to the current user)
# ---------------------------------------------------------------------------


@router.get(
    "/history/runs", dependencies=[Depends(require_workspace_enabled)]
)
async def history_list_runs(
    current_user: User = Depends(get_current_user),
) -> dict:
    """List this user's persisted runs, newest first."""
    return {"runs": await history_store.list_runs(current_user.id)}


@router.get(
    "/history/runs/{run_id}", dependencies=[Depends(require_workspace_enabled)]
)
async def history_get_run(
    run_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """One run (for re-open / re-export). 404 if missing or not this user's."""
    run = await history_store.get_run(current_user.id, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Run not found"
        )
    return run


@router.get(
    "/history/threads", dependencies=[Depends(require_workspace_enabled)]
)
async def history_list_threads(
    current_user: User = Depends(get_current_user),
) -> dict:
    """List this user's threads, most-recently-updated first."""
    return {"threads": await history_store.list_threads(current_user.id)}


@router.get(
    "/history/threads/{thread_id}",
    dependencies=[Depends(require_workspace_enabled)],
)
async def history_get_thread(
    thread_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """One thread's messages. 404 if missing or not this user's."""
    thread = await history_store.get_thread(current_user.id, thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Thread not found"
        )
    return thread
