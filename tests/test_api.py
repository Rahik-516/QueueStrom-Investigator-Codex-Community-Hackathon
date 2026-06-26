"""
Integration tests for the FastAPI surface.

The LLM is mocked via the ``stage_llm`` fixture so these tests run offline.
We exercise the documented error contract:

    400 - malformed JSON body
    422 - semantic validation error
    500 - internal error (no stack-trace leak)
"""

from __future__ import annotations

import json

import pytest

from schemas import (
    CaseType,
    Department,
    EvidenceVerdict,
    ResponseModel,
    Severity,
)


def _valid_payload() -> dict:
    return {
        "ticket_id": "T-1",
        "customer_id": "C-1",
        "complaint": "I sent money to a merchant but never received the goods.",
        "transaction_history": [
            {
                "transaction_id": "TX-1",
                "timestamp": "2026-06-25T10:00:00Z",
                "amount": -500.0,
                "currency": "BDT",
                "counterparty": "MerchantX",
                "direction": "debit",
                "status": "completed",
            }
        ],
    }


def _mock_response(**overrides) -> ResponseModel:
    base = dict(
        ticket_id="T-1",
        case_type=CaseType.WRONG_TRANSFER,
        department=Department.DISPUTE_RESOLUTION,
        severity=Severity.HIGH,
        evidence_verdict=EvidenceVerdict.CONSISTENT,
        relevant_transaction_id="TX-1",
        reasoning="Matches on amount and counterparty.",
        customer_reply="We are reviewing the transaction.",
        recommended_next_action="Pull merchant settlement records.",
    )
    base.update(overrides)
    return ResponseModel(**base)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
    # Headers we expect from the request-context middleware.
    assert "x-request-id" in r.headers
    assert "x-response-time-ms" in r.headers


# ---------------------------------------------------------------------------
# Happy path - LLM mocked
# ---------------------------------------------------------------------------

def test_analyze_ticket_happy_path(client, stage_llm):
    stage_llm(_mock_response())
    r = client.post("/analyze-ticket", json=_valid_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticket_id"] == "T-1"
    assert body["case_type"] == "wrong_transfer"
    assert body["evidence_verdict"] == "consistent"
    assert body["severity"] == "high"


# ---------------------------------------------------------------------------
# 400 - malformed JSON
# ---------------------------------------------------------------------------

def test_malformed_json_returns_400(client):
    r = client.post(
        "/analyze-ticket",
        content="{not valid json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "malformed_json"
    assert body["request_id"] == r.headers["x-request-id"]


def test_empty_body_returns_422(client):
    # Empty body is *valid* JSON (``null``) but fails Pydantic validation.
    r = client.post("/analyze-ticket", json=None)
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


# ---------------------------------------------------------------------------
# 422 - semantic validation
# ---------------------------------------------------------------------------

def test_short_complaint_returns_422(client):
    payload = _valid_payload()
    payload["complaint"] = "hi"
    r = client.post("/analyze-ticket", json=payload)
    assert r.status_code == 422


def test_unknown_field_returns_422(client):
    payload = _valid_payload()
    payload["rogue_field"] = "oops"
    r = client.post("/analyze-ticket", json=payload)
    assert r.status_code == 422


def test_invalid_enum_returns_422(client):
    payload = _valid_payload()
    payload["transaction_history"][0]["direction"] = "sideways"
    r = client.post("/analyze-ticket", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 500 - internal error path, no stack-trace leak
# ---------------------------------------------------------------------------

def test_internal_error_returns_500_without_stacktrace(client, stage_llm):
    stage_llm(RuntimeError("kaboom"))
    r = client.post("/analyze-ticket", json=_valid_payload())
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal_error"
    # Critical: never leak the raw exception.
    assert "kaboom" not in json.dumps(body)
    assert "Traceback" not in json.dumps(body)


# ---------------------------------------------------------------------------
# Header propagation
# ---------------------------------------------------------------------------

def test_request_id_header_is_echoed(client, stage_llm):
    stage_llm(_mock_response())
    r = client.post(
        "/analyze-ticket",
        json=_valid_payload(),
        headers={"x-request-id": "my-correlation-id"},
    )
    assert r.headers["x-request-id"] == "my-correlation-id"