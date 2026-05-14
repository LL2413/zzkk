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
echo "=== 3. 板块资金流 (watchlist aggregation) ==="
"$PY" scripts/enrich_sector_flow.py "${ARGS[@]}"

echo ""
echo "=== 4. 估值口径 (PE / PB / PS) ==="
"$PY" scripts/enrich_valuation.py "${ARGS[@]}"

echo ""
echo "All enrichments complete."
