# QueueStorm Investigator

A FastAPI micro-service that triages digital-finance support tickets using an
LLM that is **structurally constrained** by [`instructor`](https://github.com/jxnl/instructor)
to emit a strict Pydantic V2 schema, plus **deterministic regex guardrails**
that hard-block any unsafe language before it can ever reach a customer.



---

## TL;DR

```bash
# 1. install
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. configure
cp .env.example .env                                  # then edit LLM_API_KEY

# 3. run
python main.py                                        # or: uvicorn main:app --reload
```

```bash
# health
curl -s http://localhost:8000/health

# analyze
curl -s -X POST http://localhost:8000/analyze-ticket \
     -H 'content-type: application/json' \
     -d @sample_ticket.json
```

---

## Endpoints

### `GET /health`

Liveness probe. Always returns `{"status": "ok"}` if the process is alive.
Targets a sub-60s response by definition.

### `POST /analyze-ticket`

Accepts a ticket + transaction history and returns a fully structured triage
decision. Targets a sub-30s response.

**Request body** (see `schemas.py`):

```jsonc
{
  "ticket_id": "TCK-001",
  "customer_id": "CUST-42",
  "complaint": "I sent 250 to merchant @QuickMart yesterday but never received the item.",
  "transaction_history": [
    {
      "transaction_id": "TX-1001",
      "timestamp": "2026-06-25T14:32:00Z",
      "amount": -250.00,
      "currency": "USD",
      "counterparty": "QuickMart",
      "direction": "debit",
      "status": "completed",
      "reference": "QMK-9988"
    }
  ]
}
```

**Response body**:

```jsonc
{
  "ticket_id": "TCK-001",
  "case_type": "payment_failed",
  "department": "payments_ops",
  "severity": "medium",
  "evidence_verdict": "consistent",
  "relevant_transaction_id": "TX-1001",
  "reasoning": "The customer claims a 250 USD debit to QuickMart ...",
  "customer_reply": "Thank you for reaching out ...",
  "recommended_next_action": "Pull merchant settlement records for TX-1001 ..."
}
```

**Error contract**

| Code | Meaning                              |
|------|--------------------------------------|
| 400  | Malformed JSON body                  |
| 422  | Semantic validation failure          |
| 500  | Internal error (no stack-trace leak) |

---

## MODELS

The application is provider-agnostic via `LLM_PROVIDER`:

| `LLM_PROVIDER` | `LLM_MODEL` (recommended)               | Notes                                       |
|----------------|-----------------------------------------|---------------------------------------------|
| `openai`       | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1`      | Default. Fast + cheap, fully tested.        |
| `anthropic`    | `claude-3-5-sonnet-latest`, `claude-3-haiku-20240307` | Stronger reasoning, longer context. |

`instructor` patches both providers to guarantee 100 % schema adherence; the
Pydantic V2 model in `schemas.py` is the single source of truth.

```env
LLM_PROVIDER=openai          # or "anthropic"
LLM_MODEL=gpt-4o-mini        # any chat-completions model
LLM_API_KEY=sk-...            # required
LLM_KWARGS_JSON={"temperature":0.0,"max_tokens":800}   # optional
```

---

## Architecture

```
                ┌────────────────────────┐
  client ─POST─►│   FastAPI main.py      │── JSON schema ──┐
                │   - request id         │                 │
                │   - timing             │                 ▼
                │   - error handlers     │      ┌─────────────────────┐
                │                        │      │  llm_engine.py      │
                │                        │      │  - instructor       │
                │                        │      │  - CoT system prompt│
                │                        │      │  - prompt-injection │
                │                        │      │    defense          │
                │                        │      └──────────┬──────────┘
                │                        │                 │ ResponseModel
                │                        │                 ▼
                │                        │      ┌─────────────────────┐
                │                        │      │  guardrails.py      │
                │                        │      │  - regex scan       │
                │                        │      │  - safe fallback    │
                │                        │      └──────────┬──────────┘
                └────────────┬───────────┘                 │
                             ▼                             ▼
                       200 + sanitised JSON      400 / 422 / 500
```

### Key design choices

* **Strict Pydantic V2** (`extra="forbid"`, `strict=True`) means we never
  silently accept unknown fields or coerce types - bad payloads get a clean
  422.
* **`instructor` for schema enforcement.** The LLM *cannot* return prose or
  malformed JSON; retries happen inside the library until it conforms.
* **Defense in depth.** The system prompt tells the LLM to follow safety
  rules, *and* `guardrails.py` independently scans the output with regex. If
  the LLM drifts, the offending field is replaced with a hard-coded fallback.
* **Prompt-injection defense.** The complaint field is treated as untrusted
  data; instructions inside it are ignored and noted in the reasoning field.
* **No stack-trace leakage.** The catch-all handler returns a generic
  message; the real exception is logged server-side.

---

## Project layout

```
.
├── main.py            # FastAPI app, routes, middleware, error handlers
├── schemas.py         # Pydantic V2 request/response models and enums
├── llm_engine.py      # instructor client + Chain-of-Thought system prompt
├── guardrails.py      # regex-based safety scanner + safe fallbacks
├── requirements.txt
├── Dockerfile         # python:3.11-slim, multi-stage, non-root, healthcheck
├── .env.example
└── README.md
```

---

## Testing

The project ships with an offline test suite (LLM is mocked) plus curl-based
and Python-based smoke / load scripts.

```bash
# Offline suite - 44 tests, runs without an API key.
python -m pytest -q

# End-to-end smoke (server must be running on :8000).
bash scripts/smoke.sh

# Lightweight load harness - measures p50/p95/p99 latency.
python scripts/load_test.py --base http://localhost:8000 --n 200 --concurrency 20
```

Test layout:

| File | What it locks down |
|---|---|
| `tests/test_guardrails.py` | Every credential/refund regex, false-positive guard, immutability |
| `tests/test_api.py` | Health, happy path, 400 / 422 / 500 contract, header propagation |
| `tests/test_safety.py` | Prompt-injection → guardrail overwrite, system-prompt leak prevention |
| `tests/test_entities.py` | Multilingual complaints, unicode counterparties, length limits |

---

## Running with Docker

```bash
docker build -t queuestorm-investigator .
docker run --rm -p 8000:8000 \
  -e LLM_PROVIDER=openai \
  -e LLM_MODEL=gpt-4o-mini \
  -e LLM_API_KEY=$OPENAI_API_KEY \
  queuestorm-investigator
```

The container starts in well under five seconds and exposes `/health` on
`http://localhost:8000`.

---

## Safety guarantees (recap)

`customer_reply` and `recommended_next_action` are guaranteed to:

1. **Never** request a `PIN`, `OTP`, `password`, `CVV`, `CVC`, or "security
   code".
2. **Never** confirm a `refund`, `reversal`, `unblock`, "money back", or
   "we will send you".
3. Be replaced wholesale by a neutral fallback if any of the above is
   detected, regardless of LLM cooperation.

These are enforced by `guardrails.py:apply_safety_guardrails` and verified
in the test plan.
