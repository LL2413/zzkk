#!/usr/bin/env bash
# Run all post-fetch enrichments in sequence.
#
# Usage:
#   ./scripts/enrich_all.sh                # default: data/<today>
#   ./scripts/enrich_all.sh 20260514       # specific date
#
# Each enricher modifies snapshots in place (basic_info / valuation summary /
# SZ margin net) or writes a sidecar file (sector_flow_aggregated.json).
# All are idempotent — safe to re-run.
set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "ERROR: no python3 found" >&2
  exit 2
fi

DATE_ARG="${1:-}"
if [[ -n "$DATE_ARG" ]]; then
  ARGS=(--date "$DATE_ARG")
else
  ARGS=()
fi

echo "=== 1. basic_info (historical + hardcoded) ==="
"$PY" scripts/enrich_basic_info.py "${ARGS[@]}"

echo ""
echo "=== 2. SZ 净融资 (balance delta) ==="
"$PY" scripts/enrich_margin_net.py "${ARGS[@]}"

echo ""
echo "=== 3. EM 分档主力资金 (强口径，超大单+大单) ==="
"$PY" scripts/enrich_em_fund_flow.py "${ARGS[@]}"

echo ""
echo "=== 4. 个股资金流 fallback (THS same-day net) ==="
"$PY" scripts/enrich_fund_flow_fallback.py "${ARGS[@]}"

echo ""
echo "=== 5. 板块资金流 (watchlist aggregation) ==="
"$PY" scripts/enrich_sector_flow.py "${ARGS[@]}"

echo ""
echo "=== 6. 估值口径 (PE / PB / PS) ==="
"$PY" scripts/enrich_valuation.py "${ARGS[@]}"

echo ""
echo "=== 7. 主力流向连续天数 (streak) ==="
"$PY" scripts/enrich_streak.py "${ARGS[@]}"

echo ""
echo "=== 8. 价主背离 (divergence) ==="
"$PY" scripts/enrich_divergence.py "${ARGS[@]}"

echo ""
echo "=== 9. 个股 vs 板块 (alpha) ==="
"$PY" scripts/enrich_alpha.py "${ARGS[@]}"

echo ""
echo "=== 10. 三维信号评分 (score) ==="
"$PY" scripts/enrich_score.py "${ARGS[@]}"

echo ""
echo "All enrichments complete."
