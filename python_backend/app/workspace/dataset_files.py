"""On-disk storage for instrument datasets: retained raw uploads + ``.npz``.

Arrays are NEVER put in Mongo. A parsed dataset's arrays are written as one
compressed ``.npz`` under ``INSTRUMENT_DATA_DIR/npz/<dataset_id>.npz``; the raw
upload is retained under ``INSTRUMENT_DATA_DIR/raw/<dataset_id><ext>`` so a
failed parse can be retried (and a fixed parser re-run) without re-uploading.
Everything is addressed by the Mongo dataset id, so paths are never derived
from user-supplied names.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

import numpy as np

from app.core import config

_SAFE_EXT = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def data_dir() -> str:
    return config.INSTRUMENT_DATA_DIR


def _ensure(sub: str) -> str:
    path = os.path.join(data_dir(), sub)
    os.makedirs(path, exist_ok=True)
    return path


def safe_extension(filename: Optional[str]) -> str:
    """The upload's extension if it is a plain short token, else ''."""
    if not filename:
        return ""
    i = filename.rfind(".")
    ext = filename[i:].lower() if i >= 0 else ""
    return ext if _SAFE_EXT.match(ext) else ""


def raw_path_for(dataset_id: str, filename: Optional[str]) -> str:
    return os.path.join(_ensure("raw"), f"{dataset_id}{safe_extension(filename)}")


def npz_path_for(dataset_id: str) -> str:
    return os.path.join(_ensure("npz"), f"{dataset_id}.npz")


def save_arrays(dataset_id: str, arrays: Dict[str, np.ndarray]) -> str:
    """Write the arrays as one compressed .npz; returns the path.

    Written to a temp name and renamed so a crash mid-write never leaves a
    truncated file behind under the final name.
    """
    final = npz_path_for(dataset_id)
    tmp = final + ".part"
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, **arrays)
    os.replace(tmp, final)
    return final


def load_arrays(npz_path: str) -> Dict[str, np.ndarray]:
    """Load every array from a dataset .npz into memory (no pickles)."""
    with np.load(npz_path, allow_pickle=False) as npz:
        return {name: npz[name] for name in npz.files}


def remove_files(*paths: Optional[str]) -> None:
    """Best-effort deletion of the given paths (missing files are fine)."""
    for p in paths:
        if not p:
            continue
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
        except OSError:
            # Leave it; the pointer document is gone and a sweep can clean up.
            pass


def file_size(path: Optional[str]) -> Optional[int]:
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def json_safe(value: Any) -> Any:
    """Recursively convert numpy scalars / NaN / inf into JSON+BSON-safe values.

    Metadata is built from Python types already, but per-column stats can be
    NaN (an all-NaN channel) and JSON has no NaN -- FastAPI would emit invalid
    JSON. None is the honest encoding.
    """
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)
