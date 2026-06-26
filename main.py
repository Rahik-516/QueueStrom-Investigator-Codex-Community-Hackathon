"""
main.py
=======

FastAPI application entrypoint for the QueueStorm Investigator API.

Routes
------
* ``GET  /health``          - liveness probe.
* ``POST /analyze-ticket``  - run the investigator.

Error contract
--------------
* 400 - malformed JSON body.
* 422 - semantic validation failure (delegated to FastAPI's RequestValidationError).
* 500 - internal failure.  We never leak the stack trace to the client.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from guardrails import apply_safety_guardrails
from llm_engine import analyze_ticket, get_client
from schemas import ErrorResponse, HealthResponse, ResponseModel, TicketRequest


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("queuestorm")


# ---------------------------------------------------------------------------
# Lifespan - validate config up-front so we fail fast
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup: provider=%s model=%s",
                os.getenv("LLM_PROVIDER", "openai"),
                os.getenv("LLM_MODEL", "gpt-4o-mini"))
    # Eagerly construct the client so missing keys surface here, not on the
    # first request.
    try:
        get_client()
    except Exception as exc:  # noqa: BLE001 - we re-wrap below
        logger.error("startup_failed: %s", exc)
        raise
    yield
    logger.info("shutdown")


app = FastAPI(
    title="QueueStorm Investigator",
    version="1.0.0",
    description="AI triage for digital-finance support tickets.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middleware - request id + timing
# ---------------------------------------------------------------------------

@app.middleware("http")
async def add_request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    # Stash BEFORE awaiting the endpoint so exception handlers can read it.
    request.state.request_id = request_id
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    response.headers["x-request-id"] = request_id
    response.headers["x-response-time-ms"] = f"{elapsed_ms:.1f}"
    logger.info(
        "%s %s -> %s (%.1fms) rid=%s",
        request.method, request.url.path, response.status_code,
        elapsed_ms, request_id,
    )
    return response


def _err(status_code: int, error: str, detail: str | None,
         request_id: str | None) -> JSONResponse:
    body = ErrorResponse(error=error, detail=detail, request_id=request_id).model_dump()
    return JSONResponse(status_code=status_code, content=body)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    # FastAPI raised a validation error.  Malformed JSON surfaces here with
    # ``type == "json_invalid"`` - that maps to HTTP 400 per our contract,
    # every other validation failure maps to HTTP 422.
    errors = exc.errors()
    is_malformed_json = any(e.get("type") == "json_invalid" for e in errors)
    code = status.HTTP_400_BAD_REQUEST if is_malformed_json else status.HTTP_422_UNPROCESSABLE_ENTITY
    error_name = "malformed_json" if is_malformed_json else "validation_error"
    return _err(
        code,
        error_name,
        json.dumps(errors, default=str),
        getattr(request.state, "request_id", None),
    )


@app.exception_handler(ValidationError)
async def _pydantic_handler(request: Request, exc: ValidationError):
    return _err(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "validation_error",
        json.dumps(exc.errors(), default=str),
        getattr(request.state, "request_id", None),
    )


@app.exception_handler(json.JSONDecodeError)
async def _json_handler(request: Request, exc: json.JSONDecodeError):
    return _err(
        status.HTTP_400_BAD_REQUEST,
        "malformed_json",
        f"Invalid JSON body: {exc.msg}",
        getattr(request.state, "request_id", None),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    return _err(
        exc.status_code,
        "http_error",
        str(exc.detail),
        getattr(request.state, "request_id", None),
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    # Last-resort handler: never leak the traceback.  We log it server-side.
    logger.exception("unhandled_exception rid=%s", getattr(request.state, "request_id", None))
    return _err(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "internal_error",
        "An internal error occurred. Please retry.",
        getattr(request.state, "request_id", None),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.get("/", tags=["meta"])
async def root():
    """Service info — shown when someone visits the base URL in a browser."""
    return {
        "service": "QueueStorm Investigator AI",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": {
            "health": "GET /health",
            "analyze": "POST /analyze-ticket",
        },
    }

@app.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    """Liveness probe. Always returns 200 if the process is up."""
    return HealthResponse(status="ok")


@app.post(
    "/analyze-ticket",
    response_model=ResponseModel,
    tags=["investigator"],
    responses={
        400: {"model": ErrorResponse, "description": "Malformed JSON"},
        422: {"model": ErrorResponse, "description": "Semantic validation error"},
        500: {"model": ErrorResponse, "description": "Internal error"},
    },
)
async def analyze_ticket_endpoint(payload: TicketRequest) -> ResponseModel:
    """
    Analyse a support ticket.

    Pipeline:
        1. Validate the incoming JSON against ``TicketRequest``.
        2. Run the LLM investigator (instructor -> strict Pydantic schema).
        3. Run deterministic safety guardrails on the LLM output.
        4. Return the sanitised response.
    """

    try:
        llm_response: ResponseModel = await analyze_ticket(payload)
    except Exception:
        # Re-raise so the global 500 handler can log+respond without
        # leaking details.
        raise

    safe_response, report = apply_safety_guardrails(llm_response)

    if report.sanitised:
        logger.warning(
            "guardrails_sanitised ticket_id=%s fields=%s",
            payload.ticket_id, report.fields_replaced,
        )

    return safe_response


# ---------------------------------------------------------------------------
# Local dev entrypoint: `python main.py`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEV_RELOAD", "false").lower() == "true",
    )
