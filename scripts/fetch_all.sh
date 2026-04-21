#!/usr/bin/env bash
# Batch snapshot fetcher for the china-stock-analysis skill.
#
# Usage:
#   ./scripts/fetch_all.sh                    # default watchlist
#   ./scripts/fetch_all.sh 002281 000988      # custom symbols
#   WATCHLIST_FILE=my.txt ./scripts/fetch_all.sh
#   REFRESH=1 ./scripts/fetch_all.sh          # bypass daily cache
#
# Output layout:
#   data/YYYYMMDD/
#     market.json
#     <symbol>_snapshot.json
#     _manifest.txt       # run summary (timestamps, exit codes, file sizes)
#
# The script is idempotent per trading day: re-running will hit the daily cache
# in .cache/stock/ unless REFRESH=1 is set.

set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# --- pick python ---
if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "ERROR: no python3 found. Create a venv first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install akshare pandas" >&2
  exit 2
fi

# --- resolve watchlist ---
DEFAULT_WATCHLIST=(
  "002281"   # 光迅科技   Accelink
  "000988"   # 华工科技   HGTech
  "688008"   # 澜起科技   Montage
  "603986"   # 兆易创新   GigaDevice
  "688728"   # 格科微     GalaxyCore
  "688332"   # 中科蓝讯   Bluetrum
)

if [[ $# -gt 0 ]]; then
  WATCHLIST=("$@")
elif [[ -n "${WATCHLIST_FILE:-}" && -f "$WATCHLIST_FILE" ]]; then
  mapfile -t WATCHLIST < <(grep -E '^[0-9]{6}' "$WATCHLIST_FILE" | awk '{print $1}')
else
  WATCHLIST=("${DEFAULT_WATCHLIST[@]}")
fi

FORCE_FLAG=""
if [[ "${REFRESH:-0}" == "1" ]]; then
  FORCE_FLAG="--force"
fi

DATE_TAG="$(date +%Y%m%d)"
OUT_DIR="data/$DATE_TAG"
mkdir -p "$OUT_DIR"
MANIFEST="$OUT_DIR/_manifest.txt"
: > "$MANIFEST"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "$MANIFEST"; }

log "python: $PY"
log "out_dir: $OUT_DIR"
log "refresh: ${REFRESH:-0}"
log "symbols: ${WATCHLIST[*]}"
log ""

# --- market overview ---
log "fetch market ..."
MARKET_FILE="$OUT_DIR/market.json"
if "$PY" scripts/stock.py market $FORCE_FLAG --json > "$MARKET_FILE" 2>>"$MANIFEST"; then
  log "  ok  $(wc -c < "$MARKET_FILE") bytes → $MARKET_FILE"
else
  log "  FAIL (exit=$?) see manifest"
fi

# --- per-symbol snapshots ---
OK=0; FAIL=0
for sym in "${WATCHLIST[@]}"; do
  log "fetch $sym ..."
  out="$OUT_DIR/${sym}_snapshot.json"
  if "$PY" scripts/stock.py snapshot "$sym" $FORCE_FLAG --json > "$out" 2>>"$MANIFEST"; then
    log "  ok  $(wc -c < "$out") bytes → $out"
    OK=$((OK+1))
  else
    log "  FAIL (exit=$?) see manifest"
    FAIL=$((FAIL+1))
  fi
done

log ""
log "done: ok=$OK fail=$FAIL total=${#WATCHLIST[@]}"
log "inspect: ls -la $OUT_DIR"

[[ $FAIL -eq 0 ]]
