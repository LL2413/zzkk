#!/usr/bin/env bash
# One-shot watchlist data collection + enrichment + validation + report.
#
# Usage:
#   ./scripts/run_watchlist_analysis.sh
#   ./scripts/run_watchlist_analysis.sh --no-fetch
#   ./scripts/run_watchlist_analysis.sh --date 20260522
#   ./scripts/run_watchlist_analysis.sh --force

set -u -o pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NO_FETCH=0
NO_ENRICH=0
FORCE=0
DATE_TAG=""
OUTPUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-fetch) NO_FETCH=1; shift ;;
    --no-enrich) NO_ENRICH=1; shift ;;
    --force) FORCE=1; shift ;;
    --date) DATE_TAG="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    *) echo "ERROR: unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  echo "ERROR: no python3 found. Create a venv first." >&2
  exit 2
fi

latest_tag() {
  ls -1 data 2>/dev/null | grep -E '^[0-9]{8}$' | sort | tail -n 1
}

echo "python: $PY"
echo "cwd:    $ROOT"

if [[ "$NO_FETCH" -eq 0 && -z "$DATE_TAG" ]]; then
  DOW="$(date +%u)"
  if [[ "$FORCE" -eq 0 && ( "$DOW" == "6" || "$DOW" == "7" ) ]]; then
    DATE_TAG="$(latest_tag)"
    echo "weekend: skip live fetch, analyze latest data/$DATE_TAG. Pass --force to fetch anyway."
  else
    echo "fetching today's watchlist data ..."
    REFRESH=1 ./scripts/fetch_all.sh
    FETCH_EXIT=$?
    if [[ "$FETCH_EXIT" -ne 0 ]]; then
      echo "ERROR: fetch_all failed (exit=$FETCH_EXIT). Stop before analysis." >&2
      exit 5
    fi
    DATE_TAG="$(date +%Y%m%d)"
  fi
fi

if [[ -z "$DATE_TAG" ]]; then
  DATE_TAG="$(latest_tag)"
fi
if [[ -z "$DATE_TAG" || ! -d "data/$DATE_TAG" ]]; then
  echo "ERROR: data dir not found: data/$DATE_TAG" >&2
  exit 2
fi

if [[ "$NO_ENRICH" -eq 0 ]]; then
  echo "running enrich_all for $DATE_TAG ..."
  ./scripts/enrich_all.sh "$DATE_TAG"
  ENRICH_EXIT=$?
  if [[ "$ENRICH_EXIT" -ne 0 ]]; then
    echo "ERROR: enrich_all failed (exit=$ENRICH_EXIT)." >&2
    exit 6
  fi
fi

echo "validating data/$DATE_TAG ..."
"$PY" .claude/skills/china-stock-analysis/scripts/validate_data.py --data-dir "data/$DATE_TAG"
VALIDATE_EXIT=$?
if [[ "$VALIDATE_EXIT" -ne 0 ]]; then
  echo "ERROR: validation failed (exit=$VALIDATE_EXIT)." >&2
  exit 7
fi

if [[ -z "$OUTPUT" ]]; then
  OUTPUT="reports/watchlist_${DATE_TAG}.md"
fi
echo "generating report ..."
"$PY" scripts/analyze_watchlist.py --date "$DATE_TAG" --output "$OUTPUT"
REPORT_EXIT=$?
if [[ "$REPORT_EXIT" -ne 0 ]]; then
  echo "ERROR: report generation failed (exit=$REPORT_EXIT)." >&2
  exit 8
fi

echo
echo "done: $OUTPUT"
