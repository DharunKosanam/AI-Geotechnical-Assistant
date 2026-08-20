"""Instrument dataset routes (INSTRUMENT_PARSERS_ENABLED): artifacts + jobs.

Datasets are the numeric pool the dataset-bound calculators draw from. They
are durable (pointer doc in Mongo, arrays in .npz on disk) and user-scoped.

Registration mirrors the highlights feature: :func:`register` includes this
router ONLY when ``INSTRUMENT_PARSERS_ENABLED`` is on, so a flag-off process
has a route table byte-for-byte identical to before the feature (these paths
simply do not exist). Belt and braces, every route ALSO carries a call-time
gate that answers exactly like an absent route (404 ``{"detail":"Not Found"}``)
when the flag is off, and the standard workspace gate after that.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, status

from app.core import config
from app.dependencies.auth import get_current_user
from app.workspace import dataset_files, dataset_store, instrument_ingest
from app.workspace.routes import require_workspace_enabled
from models import User

router = APIRouter(prefix="/api/workspace", tags=["workspace-datasets"])


def require_instrument_parsers_enabled() -> None:
    """404 exactly like a missing route when the flag is off (read at call
    time), then the usual WORKSPACE_ENABLED gate."""
    if not config.INSTRUMENT_PARSERS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    require_workspace_enabled()


@router.get(
    "/datasets", dependencies=[Depends(require_instrument_parsers_enabled)]
)
async def list_datasets(
    current_user: User = Depends(get_current_user),
) -> dict:
    """This user's datasets, newest first, each with its job state/progress,
    detected badge, compact metadata and segments (no raw header)."""
    return {"datasets": await dataset_store.list_datasets(current_user.id)}


@router.get(
    "/datasets/jobs/{job_id}",
    dependencies=[Depends(require_instrument_parsers_enabled)],
)
async def get_parse_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Poll a parse job: ``{state, progress, error, ...}``. 404 if not this user's."""
    job = await dataset_store.get_job(current_user.id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


@router.get(
    "/datasets/{dataset_id}",
    dependencies=[Depends(require_instrument_parsers_enabled)],
)
async def get_dataset(
    dataset_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """One dataset artifact's full metadata (incl. raw header lines, shapes,
    dtypes, warnings, segments, file paths). Never the arrays."""
    ds = await dataset_store.get_dataset(current_user.id, dataset_id, full=True)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return ds


@router.post(
    "/datasets/{dataset_id}/retry",
    dependencies=[Depends(require_instrument_parsers_enabled)],
)
async def retry_dataset(
    dataset_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Re-queue the parse of a (failed) dataset from its retained raw upload."""
    ds = await instrument_ingest.retry_dataset(current_user.id, dataset_id, background_tasks)
    if ds is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    return ds


@router.delete(
    "/datasets/{dataset_id}",
    dependencies=[Depends(require_instrument_parsers_enabled)],
)
async def delete_dataset(
    dataset_id: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Remove a dataset: pointer doc, its jobs, the .npz and the raw upload."""
    doc = await dataset_store.delete_dataset(current_user.id, dataset_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset not found")
    dataset_files.remove_files(doc.get("npz_path"), doc.get("raw_path"))
    return {"deleted": dataset_id}


_registered = False


def register(app: FastAPI) -> None:
    """Include the dataset routes ONLY when INSTRUMENT_PARSERS_ENABLED is on.
    Idempotent (tests call it from a fixture)."""
    global _registered
    if config.INSTRUMENT_PARSERS_ENABLED and not _registered:
        app.include_router(router)
        _registered = True
