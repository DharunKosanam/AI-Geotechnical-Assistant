"""
Shared pytest configuration for the python_backend test suite.

Layout
------
tests/unit/         Pure-function tests (Problems 1-5). Fast, deterministic, no
                    external services. This is what the default ``pytest`` run
                    executes (see pytest.ini: ``testpaths = tests/unit``).
tests/integration/  Tests that touch the FastAPI app / MongoDB / Groq. Opt-in:
                    ``pytest tests/integration``. The heavy fixtures and autouse
                    Groq/Mongo mocks live in tests/integration/conftest.py so the
                    unit run never imports the app or hits a service.
tests/integration/test_e2e.py
                    Live-server smoke harness (needs ``uvicorn`` on :8000). Not a
                    pytest module -- run it directly:
                    ``python tests/integration/test_e2e.py``. It is excluded from
                    collection via ``--ignore`` in pytest.ini.

This top-level conftest is intentionally light: it holds nothing that would pull
the FastAPI app into the unit run.
"""
