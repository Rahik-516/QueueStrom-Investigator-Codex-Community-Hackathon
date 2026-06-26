"""
guardrails.py
=============

Deterministic, regex-based safety scanner for the LLM-generated response.

Why deterministic?
------------------
The system prompt instructs the LLM to obey safety rules, but a model is a
probabilistic system.  We belt-and-braces the output here with case-insensitive
regex checks.  If anything dangerous is detected we *replace* the offending
field with a hard-coded safe fallback before it ever reaches the customer.

What we scan
------------
We scan both ``customer_reply`` and ``recommended_next_action`` for:

1.  Requests for credentials  - PIN, OTP, password, CVV (and common variants).
2.  Confirmation of money movement we are not authorised to make -
    refund, reversed, reversal, unblock(ed), credited back, etc.

If any of those patterns match we replace the entire field with the
``SAFE_FALLBACK`` string and record what happened so the caller can log it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from schemas import ResponseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Case-insensitive.  We use word boundaries so "pinpoint" does NOT match.
_CREDENTIAL_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\bpin\b", re.IGNORECASE),
    re.compile(r"\botp\b", re.IGNORECASE),
    re.compile(r"\bone[\s-]?time[\s-]?code\b", re.IGNORECASE),
    re.compile(r"\bpassword\b", re.IGNORECASE),
    re.compile(r"\bpasscode\b", re.IGNORECASE),
    re.compile(r"\bcvv\b", re.IGNORECASE),
    re.compile(r"\bcvc\b", re.IGNORECASE),
    re.compile(r"\bsecurity\s*code\b", re.IGNORECASE),
)

# Confirmation of money movement we are NOT allowed to make.
# We match the verb/stem so "we will refund", "has been refunded", "refunding"
# are all caught.
_REFUND_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\brefund(?:ed|ing|s)?\b", re.IGNORECASE),
    re.compile(r"\brevers(?:e|ed|al|ing)\b", re.IGNORECASE),
    re.compile(r"\bunblock(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\bcredit(?:ed)?\s*back\b", re.IGNORECASE),
    re.compile(r"\bmoney\s*back\b", re.IGNORECASE),
    re.compile(r"\bwe\s+will\s+(?:send|transfer|return)\s+you\b", re.IGNORECASE),
)

SAFE_FALLBACK = (
    "We have noted your concern and our team is reviewing the transaction. "
    "Any eligible amount will be returned through official channels after "
    "verification."
)

SAFE_NEXT_ACTION_FALLBACK = (
    "Assign the case to the relevant operations queue for manual review. "
    "Do not contact the customer about money movement until verification is "
    "complete."
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class GuardrailReport:
    """What the scanner changed - useful for logging / audit."""

    sanitised: bool = False
    triggered_credentials: List[str] = field(default_factory=list)
    triggered_refunds: List[str] = field(default_factory=list)
    fields_replaced: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def _scan(text: str) -> Tuple[List[str], List[str]]:
    """Return (credential_hits, refund_hits) for a piece of text."""
    cred_hits: List[str] = []
    for pat in _CREDENTIAL_PATTERNS:
        if pat.search(text):
            cred_hits.append(pat.pattern)
    ref_hits: List[str] = []
    for pat in _REFUND_PATTERNS:
        if pat.search(text):
            ref_hits.append(pat.pattern)
    return cred_hits, ref_hits


def apply_safety_guardrails(response: ResponseModel) -> Tuple[ResponseModel, GuardrailReport]:
    """
    Scan ``response`` and replace any field that violates safety rules.

    The function returns a *new* ``ResponseModel`` (Pydantic models are
    immutable in v2 when you call ``model_copy``) so callers don't have to
    worry about mutation.  A ``GuardrailReport`` is returned alongside it
    for observability.
    """

    report = GuardrailReport()
    updates: dict[str, str] = {}

    for field_name in ("customer_reply", "recommended_next_action"):
        original = getattr(response, field_name)
        cred_hits, ref_hits = _scan(original)

        if cred_hits or ref_hits:
            report.sanitised = True
            report.triggered_credentials.extend(cred_hits)
            report.triggered_refunds.extend(ref_hits)
            report.fields_replaced.append(field_name)

            fallback = (
                SAFE_FALLBACK
                if field_name == "customer_reply"
                else SAFE_NEXT_ACTION_FALLBACK
            )
            updates[field_name] = fallback

            logger.warning(
                "guardrail_triggered field=%s credential_hits=%d refund_hits=%d",
                field_name, len(cred_hits), len(ref_hits),
            )

    if not updates:
        return response, report

    # Build the sanitised model.  `model_copy(update=...)` returns a new
    # validated instance, so downstream Pydantic invariants still apply.
    safe = response.model_copy(update=updates)
    return safe, report