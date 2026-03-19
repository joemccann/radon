#!/bin/bash
set -euo pipefail

cd /Users/joemccann/dev/apps/finance/radon

# Run evaluate.py tests only (fast subset)
echo "Running evaluate tests..." >&2
python3 -m pytest scripts/tests/test_evaluate.py -v --tb=short 2>&1 | tail -30

# Syntax check all modified scripts
echo "Syntax check..." >&2
python3 -m py_compile scripts/evaluate.py
python3 -m py_compile scripts/fetch_ticker.py
python3 -m py_compile scripts/fetch_flow.py
python3 -m py_compile scripts/fetch_options.py
python3 -m py_compile scripts/fetch_oi_changes.py
python3 -m py_compile scripts/fetch_analyst_ratings.py
python3 -m py_compile scripts/fetch_news.py

echo "All checks passed" >&2
