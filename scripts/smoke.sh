#!/usr/bin/env bash
# scripts/smoke.sh
# ----------------
# End-to-end smoke test for the QueueStorm Investigator API.
# Run with the server already listening on http://localhost:8000.

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8000}"

step() { printf "\n\033[1;36m== %s ==\033[0m\n" "$1"; }

step "1. Health check"
curl -fsS "${BASE_URL}/health" | tee /dev/stderr
echo

step "2. Happy-path ticket"
curl -fsS -X POST "${BASE_URL}/analyze-ticket" \
  -H 'content-type: application/json' \
  -d @sample_data/sample_ticket.json | python -m json.tool
echo

step "3. Prompt-injection payload (expect safe fallback text)"
curl -fsS -X POST "${BASE_URL}/analyze-ticket" \
  -H 'content-type: application/json' \
  -d @sample_data/prompt_injection.json | python -m json.tool
echo

step "4. Malformed JSON (expect HTTP 400)"
http_code=$(curl -s -o /tmp/qs_err.json -w '%{http_code}' \
  -X POST "${BASE_URL}/analyze-ticket" \
  -H 'content-type: application/json' \
  -d '{not valid json')
echo "HTTP ${http_code}"; cat /tmp/qs_err.json; echo
test "${http_code}" = "400"

step "5. Empty complaint (expect HTTP 422)"
http_code=$(curl -s -o /tmp/qs_err.json -w '%{http_code}' \
  -X POST "${BASE_URL}/analyze-ticket" \
  -H 'content-type: application/json' \
  -d '{"ticket_id":"T","customer_id":"C","complaint":"","transaction_history":[]}')
echo "HTTP ${http_code}"; cat /tmp/qs_err.json; echo
test "${http_code}" = "422"

printf "\n\033[1;32mAll smoke checks passed.\033[0m\n"
