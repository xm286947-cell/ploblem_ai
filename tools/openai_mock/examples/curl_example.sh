#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${OPENAI_MOCK_BASE_URL:-http://127.0.0.1:8000}"

curl -fsS "${BASE_URL}/__mock__/scenario" \
  -H "Content-Type: application/json" \
  -d '{
    "scenario_key": "default",
    "payload": "hello from curl",
    "behavior": {}
  }'

echo

curl -fsS "${BASE_URL}/v1/responses" \
  -H "Authorization: Bearer mock-key" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "mock-gpt",
    "input": "hello"
  }'

echo
