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
  "002281"   # 光迅科技   Accelink (光模块)
  "000988"   # 华工科技   HGTech (激光+光模块)
  "688008"   # 澜起科技   Montage (DDR 接口芯片)
  "603986"   # 兆易创新   GigaDevice (存储)
  "688728"   # 格科微     GalaxyCore (CIS 图像传感器)
  "688332"   # 中科蓝讯   Bluetrum (蓝牙 SoC)
  "688046"   # 药康生物   GemPharmatech (模式动物/CRO)
  "688380"   # 中微半导   AME-Semi (模拟芯片)
  "688123"   # 聚辰股份   Giantec (EEPROM 存储)
  "688550"   # 瑞联新材   Valiant (OLED/液晶材料)
  "688208"   # 道通科技   Autel (汽车诊断/充电桩)
  "002475"   # 立讯精密   Luxshare (消费电子代工)
  "300458"   # 全志科技   Allwinner (AIoT SoC)
  "601869"   # 长飞光纤   YOFC (光纤光缆)
  "600522"   # 中天科技   Zhongtian Tech (光纤+海缆+储能)
  "600487"   # 亨通光电   Hengtong (光纤+海缆)
  "300395"   # 菲利华     Feilihua (石英材料/半导体耗材)
  "300408"   # 三环集团   Three Circle (MLCC 电子元件)
  "603256"   # 宏和科技   Honghe Tech (电子布/玻纤，半导体封装+PCB)
  "603773"   # 沃格光电   WG Tech (光电玻璃精加工)
  "000021"   # 深科技     SDG (存储芯片封测/电子制造)
  "688627"   # 精智达     EIT (半导体/显示测试设备)
  "688206"   # 概伦电子   Primarius (EDA 软件)
  "688521"   # 芯原股份   VeriSilicon (芯片设计服务/IP)
  "688047"   # 龙芯中科   Loongson (国产 CPU)
  "600845"   # 宝信软件   Baosight (工业软件/IDC)
  "300499"   # 高澜股份   Gaolan (电力电子温控/液冷)
  "002837"   # 英维克     Envicool (精密温控/数据中心液冷)
  "002156"   # 通富微电   TFME (半导体封测)
  "600584"   # 长电科技   JCET (半导体封测)
  "688981"   # 中芯国际   SMIC (晶圆代工)
  "688347"   # 华虹公司   HuaHong (晶圆代工)
  "601138"   # 工业富联   FII (服务器/AI 算力硬件)
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
  MARKET_BYTES=$(wc -c < "$MARKET_FILE")
  if (( MARKET_BYTES > 1024 )); then
    log "  ok  ${MARKET_BYTES} bytes → $MARKET_FILE"
  else
    # Mirror fetch_all.ps1 guard: sub-1KB market.json is a truncated/empty write
    # that later tripped the validator on "unreadable json". Delete it so the
    # next step sees a missing market, not a corrupt one.
    log "  FAIL ($MARKET_BYTES bytes is too small; treating as truncated)"
    rm -f "$MARKET_FILE"
  fi
else
  log "  FAIL (exit=$?) see manifest"
  rm -f "$MARKET_FILE"
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
