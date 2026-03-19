#!/bin/bash
set -euo pipefail

cd /Users/joemccann/dev/apps/finance/radon

# Pre-check: verify evaluate.py is syntactically valid
python3 -m py_compile scripts/evaluate.py 2>&1 || {
    echo "METRIC total_ms=999999"
    echo "METRIC single_ms=999999"
    exit 1
}

# Benchmark: Single ticker
echo "=== Single Ticker Benchmark (AAPL) ===" >&2
start_single=$(python3 -c "import time; print(int(time.time() * 1000))")
python3 scripts/evaluate.py AAPL --json > /dev/null 2>&1
end_single=$(python3 -c "import time; print(int(time.time() * 1000))")
single_ms=$((end_single - start_single))
echo "Single: ${single_ms}ms" >&2

# Benchmark: 5 tickers (sequential baseline)
echo "=== Multi-Ticker Benchmark (5 tickers) ===" >&2
start_multi=$(python3 -c "import time; print(int(time.time() * 1000))")
for ticker in AAPL MSFT NVDA GOOG TSLA; do
    python3 scripts/evaluate.py "$ticker" --json > /dev/null 2>&1 || true
done
end_multi=$(python3 -c "import time; print(int(time.time() * 1000))")
total_ms=$((end_multi - start_multi))
echo "Multi (5): ${total_ms}ms" >&2

# Output metrics for autoresearch
echo "METRIC total_ms=${total_ms}"
echo "METRIC single_ms=${single_ms}"
