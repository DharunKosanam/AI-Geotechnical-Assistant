# python_backend test suite

## One command (green/red health check)

```bash
cd python_backend
pytest
```

Runs the **unit** suite only (`testpaths = tests/unit` in `pytest.ini`): fast,
deterministic, no MongoDB / Groq / live server. This is the green/red signal for
the Problems 1-5 logic.

## Layout

```
tests/
  conftest.py                 # light shared config (no FastAPI import)
  unit/                       # pure-function tests -- the default `pytest` run
    test_query_rewriter.py        # Problem 1  (llm_service rewriter + helpers)
    test_rerank_threshold.py      # Problem 2  (_apply_rerank_threshold)
    test_duplicate_preflight.py   # Problem 3B (kb_admin._duplicate_preflight)
    test_citation_filter.py       # Problem 4  (citation_filter)
    test_combined_search_filter.py# Problem 5  (_combined_search_filter)
  integration/                # opt-in: need a DB (and a live server for e2e)
    conftest.py                   # async_client + autouse Groq/Mongo mocks
    test_api.py                   # in-process endpoint tests (marked `integration`)
    test_e2e.py                   # live-server smoke harness (NOT a pytest module)
```

## Other commands

```bash
pytest tests/integration              # in-process API tests (needs a DB)
python tests/integration/test_e2e.py  # live e2e smoke (start uvicorn on :8000 first)
```

`test_e2e.py` is a standalone script; it is excluded from pytest collection via
`--ignore` in `pytest.ini`. Each unit test file is also runnable standalone, e.g.
`python tests/unit/test_query_rewriter.py`.
