"""Report generation -- EXTENSION SEAM ONLY (no implementation yet).

This module marks where a future "Generate report (.docx)" action will plug in.
It is deliberately empty of logic: the report build itself is OUT OF SCOPE for
this pass and will be added later (project-doc generator, Dr. Lin's templates).

HOW IT WILL PLUG IN (drop-in, no changes needed elsewhere)
----------------------------------------------------------
A report is generated from a persisted run's ``result_object`` -- the SAME
structured object the export already consumes -- so everything a report needs is
already stored and user-scoped:

    result_object = {
        "calculator_id": ..., "calculator_name": ..., "source_file": ...,
        "reference": ...,
        "layers":   [...],   # detected layers (on-screen table)
        "metadata": {...},   # run params + method
        "tables":   [...],   # standard export schema (per-reading data, etc.)
        "summary":  {...},   # metadata sheet key/values
    }

The future wiring mirrors the export path exactly:
    * a ``ReportBuilder`` turns a ``result_object`` into ``.docx`` bytes;
    * a route ``GET /api/workspace/report/{run_id}`` fetches the run by
      ``run_id`` + ``user_id`` (via ``history_store.get_run``) and streams the
      bytes, returning a clean 404 when the run is missing/foreign;
    * the frontend shows a "Generate report" button on a result whenever the
      calculator opts in -- alongside the existing "Export to Excel" button.

The AI interpretation text is NOT part of ``result_object`` (kept out of the
durable run on purpose); a report that wants it would take it separately from
the thread message, never from the export/run object.
"""

from __future__ import annotations

from typing import Any, Dict, Protocol, runtime_checkable


@runtime_checkable
class ReportBuilder(Protocol):
    """Interface a future report generator will implement (NOT yet built).

    Implementations turn a persisted run ``result_object`` into document bytes
    (e.g. a ``.docx``). Intentionally unimplemented in this pass.
    """

    # File extension the builder produces, e.g. "docx" (used for the download
    # filename). Declared here only to document the interface.
    extension: str

    def build(self, result_object: Dict[str, Any]) -> bytes:  # pragma: no cover
        """Render ``result_object`` to document bytes. To be implemented later."""
        ...


# No builders are registered yet. A future pass adds a registry here (or a
# per-calculator ``report_builder`` hook) plus the route + button described
# above -- with ZERO changes to routing/export/history/UI beyond that.
