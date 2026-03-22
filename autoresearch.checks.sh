#!/bin/bash
set -euo pipefail

cd /Users/joemccann/dev/apps/finance/radon

# Verify test count hasn't dropped
result=$(python3.13 -m pytest scripts/tests/ scripts/trade_blotter/test_blotter.py scripts/trade_blotter/test_integration.py \
  --ignore=scripts/tests/test_menthorq_integration.py \
  -n 4 -q --tb=short --no-header 2>&1)

passed=$(echo "$result" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || echo "0")
failed=$(echo "$result" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || echo "0")

if [ "$failed" -gt 0 ]; then
  echo "FAIL: $failed tests failed"
  echo "$result" | grep -E "FAILED|ERROR" | tail -20
  exit 1
fi

if [ "$passed" -lt 1327 ]; then
  echo "FAIL: Only $passed tests passed (expected >= 1327)"
  exit 1
fi

echo "OK: $passed passed, $failed failed"
