"""
Unit tests for ``guardrails.apply_safety_guardrails``.

We exercise every pattern the scanner is supposed to catch, plus a few
adversarial variants (mixed case, embedded in a longer sentence, with
punctuation).  We also verify that *clean* text is returned untouched
(no false positives).
"""

from __future__ import annotations

import pytest

from guardrails import (
    SAFE_FALLBACK,
    SAFE_NEXT_ACTION_FALLBACK,
    apply_safety_guardrails,
)
from schemas import CaseType, Department, EvidenceVerdict, ResponseModel, Severity


def _build(**overrides) -> ResponseModel:
    base = dict(
        ticket_id="T-1",
        case_type=CaseType.OTHER,
        department=Department.CUSTOMER_SUPPORT,
        severity=Severity.LOW,
        evidence_verdict=EvidenceVerdict.INSUFFICIENT_DATA,
        relevant_transaction_id=None,
        reasoning="test",
        customer_reply="Thank you for reaching out.",
        recommended_next_action="Assign to support queue.",
    )
    base.update(overrides)
    return ResponseModel(**base)


# ---------------------------------------------------------------------------
# Clean text - no replacement
# ---------------------------------------------------------------------------

def test_clean_text_passes_through_unchanged():
    resp = _build(
        customer_reply="We are reviewing your case and will be in touch shortly.",
        recommended_next_action="Assign to the standard dispute queue.",
    )
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is False
    assert safe.customer_reply == resp.customer_reply
    assert safe.recommended_next_action == resp.recommended_next_action


# ---------------------------------------------------------------------------
# Credential patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "snippet",
    ["Please share your PIN", "send me the otp", "your password is wrong",
     "share your cvv", "what is the cvc", "give me the security code"],
)
def test_credential_patterns_trigger_replacement(snippet: str):
    resp = _build(customer_reply=f"Hello, {snippet} so we can proceed.")
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is True
    assert "customer_reply" in report.fields_replaced
    assert safe.customer_reply == SAFE_FALLBACK


def test_case_insensitive_pin_detection():
    resp = _build(customer_reply="Please confirm your pin number.")
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is True
    assert safe.customer_reply == SAFE_FALLBACK


def test_word_boundary_does_not_flag_pinpoint():
    # "pinpoint" contains "pin" - the scanner must NOT trigger on it.
    resp = _build(customer_reply="We can pinpoint the issue from the logs.")
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is False
    assert safe.customer_reply == resp.customer_reply


# ---------------------------------------------------------------------------
# Refund / reversal / unblock patterns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "snippet",
    ["we will refund you", "the amount has been refunded",
     "we have reversed the transaction", "your account is unblocked",
     "we will credit back the money", "money back is on the way",
     "we will send you the funds"],
)
def test_refund_patterns_trigger_replacement(snippet: str):
    resp = _build(customer_reply=f"Hello, {snippet} within 24 hours.")
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is True
    assert "customer_reply" in report.fields_replaced
    assert safe.customer_reply == SAFE_FALLBACK
    assert "refund" not in safe.customer_reply.lower()
    assert "revers" not in safe.customer_reply.lower()


def test_recommended_next_action_refund_uses_action_fallback():
    resp = _build(recommended_next_action="Refund the customer immediately.")
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is True
    assert safe.recommended_next_action == SAFE_NEXT_ACTION_FALLBACK


def test_both_fields_replaced_independently():
    resp = _build(
        customer_reply="Please send your PIN to verify.",
        recommended_next_action="Refund and unblock the account.",
    )
    safe, report = apply_safety_guardrails(resp)
    assert report.sanitised is True
    assert set(report.fields_replaced) == {"customer_reply", "recommended_next_action"}
    assert safe.customer_reply == SAFE_FALLBACK
    assert safe.recommended_next_action == SAFE_NEXT_ACTION_FALLBACK


# ---------------------------------------------------------------------------
# Original model is NOT mutated (Pydantic immutability contract)
# ---------------------------------------------------------------------------

def test_original_response_is_not_mutated():
    resp = _build(customer_reply="Please share your password.")
    _safe, _ = apply_safety_guardrails(resp)
    # Pydantic v2 + model_copy should leave the source untouched.
    assert resp.customer_reply == "Please share your password."
