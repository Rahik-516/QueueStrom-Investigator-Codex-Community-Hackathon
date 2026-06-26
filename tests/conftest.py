"""
Pytest configuration & shared fixtures.

Why this file exists
--------------------
The application boots an LLM client at import time.  We want unit and
integration tests to run **offline** (no real API key, no real network
calls), so we:

  1.  Inject a dummy ``LLM_API_KEY`` *before* importing the app.
  2.  Provide an ``async_test_client`` fixture that uses FastAPI's
      ``TestClient`` against the in-process app.
  3.  Expose a ``mocked_analyze`` fixture that monkeypatches
      ``main.analyze_ticket`` so each test can stage the LLM response
      deterministically.
"""

from __future__ import annotations

import os
from typing import Any, Callable

# MUST happen before any app import - the LLM client validates the key.
os.environ.setdefault("LLM_API_KEY", "test-dummy-key")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("LLM_MODEL", "gpt-4o-mini")

import pytest
from fastapi.testclient import TestClient

import main as main_module
from schemas import ResponseModel


# ---------------------------------------------------------------------------
# Sync TestClient (FastAPI runs async endpoints via anyio under the hood)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    # ``raise_server_exceptions=False`` lets our global exception handler
    # run for internal failures instead of the TestClient re-raising them.
    return TestClient(main_module.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# A factory that lets each test stage the LLM's return value.
# ---------------------------------------------------------------------------

@pytest.fixture()
def stage_llm(monkeypatch: pytest.MonkeyPatch) -> Callable[[ResponseModel | Exception], None]:
    """
    Usage::

        stage_llm(ResponseModel(...))      # happy path
        stage_llm(RuntimeError("boom"))    # 500 path
    """

    def _set(value: Any) -> None:
        async def _fake(_payload):
            if isinstance(value, BaseException):
                raise value
            return value

        monkeypatch.setattr(main_module, "analyze_ticket", _fake)

    return _set
