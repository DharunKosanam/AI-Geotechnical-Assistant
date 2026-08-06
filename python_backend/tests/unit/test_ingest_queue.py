"""Phase 0.5 queue-depth cap: ingest_try_acquire / ingest_release / depth.

Pure in-process counter logic (no DB, no server). The counter is a module
global, so the autouse fixture resets it and restores the configured cap around
every test.
"""
import pytest

from app.core import config
from app.services import rag_service as rs


@pytest.fixture(autouse=True)
def reset_queue():
    saved_cap = config.INGEST_MAX_QUEUE
    rs._ingest_inflight = 0
    yield
    rs._ingest_inflight = 0
    config.INGEST_MAX_QUEUE = saved_cap


def test_acquire_admits_up_to_cap():
    config.INGEST_MAX_QUEUE = 3
    assert rs.ingest_try_acquire() is True
    assert rs.ingest_try_acquire() is True
    assert rs.ingest_try_acquire() is True
    assert rs.ingest_queue_depth() == 3


def test_acquire_rejects_beyond_cap():
    config.INGEST_MAX_QUEUE = 2
    assert rs.ingest_try_acquire() is True
    assert rs.ingest_try_acquire() is True
    assert rs.ingest_try_acquire() is False  # backlog full
    assert rs.ingest_queue_depth() == 2


def test_release_frees_a_slot():
    config.INGEST_MAX_QUEUE = 1
    assert rs.ingest_try_acquire() is True
    assert rs.ingest_try_acquire() is False
    rs.ingest_release()
    assert rs.ingest_queue_depth() == 0
    assert rs.ingest_try_acquire() is True


def test_release_floors_at_zero():
    rs.ingest_release()
    rs.ingest_release()
    assert rs.ingest_queue_depth() == 0


def test_cap_is_read_at_call_time():
    # config/env changes take effect without re-importing the module
    config.INGEST_MAX_QUEUE = 0
    assert rs.ingest_try_acquire() is False
    config.INGEST_MAX_QUEUE = 1
    assert rs.ingest_try_acquire() is True
