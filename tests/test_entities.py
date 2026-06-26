"""
Edge-case tests for entity extraction and pipeline behaviour.

The legacy app didn't have an entity extractor module - it relied on the LLM.
These tests document the deterministic fields that *must* survive end-to-end
even when the LLM is mocked.  When the entity-extractor module is added,
this file is the natural place to add direct unit tests for it.
"""

from __future__ import annotations

import pytest

from schemas import (
    CaseType,
    Department,
    EvidenceVerdict,
    ResponseModel,
    Severity,
)


def _payload(complaint: str) -> dict:
    return {
        "ticket_id": "T-EDGE",
        "customer_id": "C-1",
        "complaint": complaint,
        "transaction_history": [],
    }


def _echo_response() -> ResponseModel:
    return ResponseModel(
        ticket_id="T-EDGE",
        case_type=CaseType.OTHER,
        department=Department.CUSTOMER_SUPPORT,
        severity=Severity.LOW,
        evidence_verdict=EvidenceVerdict.INSUFFICIENT_DATA,
        relevant_transaction_id=None,
        reasoning="Insufficient data.",
        customer_reply="Noted.",
        recommended_next_action="Wait for customer follow-up.",
    )


@pytest.mark.parametrize(
    "complaint",
    [
        "I sent 5000 BDT to 01712345678 yesterday.",  # Bangladeshi phone + BDT
        "I transferred ৳2,500 to merchant ABC.",       # unicode taka sign
        "Refund please. I paid $250 USD via Stripe.", # mixed currencies
        "cash in 1000 tk done, balance not updated",   # Banglish "tk"
        "আমি ৫০০০ টাকা পাঠিয়েছি কিন্তু পাইনি।",         # Bangla script
        "        ",                                   # whitespace - rejected by validator
    ],
)
def test_complaint_variants_round_trip(client, stage_llm, complaint: str):
    stage_llm(_echo_response())
    payload = _payload(complaint)
    r = client.post("/analyze-ticket", json=payload)
    # Whitespace-only must be rejected with 422.
    if complaint.strip() == "":
        assert r.status_code == 422
        return
    assert r.status_code == 200, r.text
    assert r.json()["ticket_id"] == "T-EDGE"


def test_multiple_amounts_does_not_crash(client, stage_llm):
    stage_llm(_echo_response())
    r = client.post(
        "/analyze-ticket",
        json=_payload("Paid 100 then 200 then 300 - total 600. Want all refunded."),
    )
    assert r.status_code == 200


def test_no_transaction_history_still_succeeds(client, stage_llm):
    stage_llm(_echo_response())
    r = client.post("/analyze-ticket", json=_payload("Random complaint with no history."))
    assert r.status_code == 200
    # Insufficient data path is deterministic.
    assert r.json()["evidence_verdict"] == "insufficient_data"


def test_very_long_complaint_within_limit(client, stage_llm):
    stage_llm(_echo_response())
    r = client.post(
        "/analyze-ticket",
        json=_payload("A" * 7000),
    )
    assert r.status_code == 200


def test_complaint_above_limit_rejected(client):
    r = client.post("/analyze-ticket", json=_payload("A" * 9000))
    assert r.status_code == 422


def test_unicode_in_counterparty(client, stage_llm):
    stage_llm(_echo_response())
    payload = _payload("Payment to কুইকমার্ট failed.")
    payload["transaction_history"] = [
        {
            "transaction_id": "TX-U1",
            "timestamp": "2026-06-25T10:00:00Z",
            "amount": -100.0,
            "currency": "BDT",
            "counterparty": "কুইকমার্ট",
            "direction": "debit",
            "status": "completed",
        }
    ]
    r = client.post("/analyze-ticket", json=payload)
    assert r.status_code == 200