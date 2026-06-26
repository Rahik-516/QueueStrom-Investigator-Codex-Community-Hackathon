"""
schemas.py
==========

Strict Pydantic V2 schemas for the QueueStorm Investigator API.

All enums are locked to the exact strings required by the problem statement,
so the LLM (via `instructor`) and the API surface stay in lock-step.

We use ``ConfigDict(strict=True)`` so Pydantic will *not* silently coerce
types - this is what gives us the "Strict Mode" required by the spec.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Enums - these are intentionally flat string enums so the LLM can emit them
# directly via `instructor` without ambiguity.
# ---------------------------------------------------------------------------

class EvidenceVerdict(str, Enum):
    """Comparison outcome between complaint and transaction history."""

    CONSISTENT = "consistent"
    INCONSISTENT = "inconsistent"
    INSUFFICIENT_DATA = "insufficient_data"


class CaseType(str, Enum):
    WRONG_TRANSFER = "wrong_transfer"
    PAYMENT_FAILED = "payment_failed"
    REFUND_REQUEST = "refund_request"
    DUPLICATE_PAYMENT = "duplicate_payment"
    MERCHANT_SETTLEMENT_DELAY = "merchant_settlement_delay"
    AGENT_CASH_IN_ISSUE = "agent_cash_in_issue"
    PHISHING_OR_SOCIAL_ENGINEERING = "phishing_or_social_engineering"
    OTHER = "other"


class Department(str, Enum):
    CUSTOMER_SUPPORT = "customer_support"
    DISPUTE_RESOLUTION = "dispute_resolution"
    PAYMENTS_OPS = "payments_ops"
    MERCHANT_OPERATIONS = "merchant_operations"
    AGENT_OPERATIONS = "agent_operations"
    FRAUD_RISK = "fraud_risk"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class Transaction(BaseModel):
    """A single entry in the customer's transaction history.

    We deliberately do NOT enable ``strict=True`` here because the wire
    format always carries ``timestamp`` as an ISO-8601 string, and we want
    Pydantic to coerce it into a ``datetime`` for downstream matching.
    All other fields are still tightly validated (no extra fields, length
    bounds on strings, the direction pattern).
    """

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(..., min_length=1, description="Unique txn id")
    timestamp: datetime = Field(..., description="ISO-8601 timestamp")
    amount: float = Field(..., description="Signed amount (negative = debit)")
    currency: str = Field(..., min_length=3, max_length=3, description="ISO-4217 code")
    counterparty: str = Field(..., min_length=1, description="Merchant or other party")
    direction: str = Field(..., pattern="^(debit|credit)$")
    status: str = Field(..., min_length=1)
    reference: Optional[str] = Field(default=None, description="Optional bank reference")


class TicketRequest(BaseModel):
    """Inbound ticket payload for `POST /analyze-ticket`."""

    model_config = ConfigDict(strict=True, extra="forbid")

    ticket_id: str = Field(..., min_length=1)
    customer_id: str = Field(..., min_length=1)
    complaint: str = Field(..., min_length=10, max_length=8000)
    transaction_history: List[Transaction] = Field(default_factory=list)

    @field_validator("complaint")
    @classmethod
    def _strip_complaint(cls, v: str) -> str:
        # Trim whitespace but keep the original casing - we do NOT alter the
        # text before the LLM sees it because the LLM needs to detect
        # injection attempts that may rely on unusual casing.
        if not v.strip():
            raise ValueError("complaint must contain non-whitespace characters")
        return v


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ResponseModel(BaseModel):
    """The structured analysis the API returns to the support agent."""

    model_config = ConfigDict(strict=True, extra="forbid")

    ticket_id: str = Field(..., min_length=1)
    case_type: CaseType
    department: Department
    severity: Severity
    evidence_verdict: EvidenceVerdict
    relevant_transaction_id: Optional[str] = Field(
        default=None,
        description="The transaction that best matches the complaint, if any.",
    )
    reasoning: str = Field(..., min_length=1, description="Internal Chain-of-Thought")
    customer_reply: str = Field(..., min_length=1)
    recommended_next_action: str = Field(..., min_length=1)

    @field_validator("customer_reply", "recommended_next_action")
    @classmethod
    def _no_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty after stripping whitespace")
        return v


# ---------------------------------------------------------------------------
# Lightweight wrappers used for error responses (kept here so the API surface
# is fully documented in one place).
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    status: str = "ok"


class ErrorResponse(BaseModel):
    model_config = ConfigDict(strict=True)
    error: str
    detail: Optional[str] = None
    request_id: Optional[str] = None