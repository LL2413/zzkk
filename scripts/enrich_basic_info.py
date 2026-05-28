#!/usr/bin/env python3
"""Backfill missing basic_info in today's snapshots from historical ones.

basic_info (公司简称/行业/股本等) is largely time-invariant. When EM/Xueqiu
endpoints are unreachable on a given fetch, we can still surface the same
descriptive metadata by lifting it from the most recent successful snapshot.

Two-stage fallback:
  1. Historical reuse — scan past data/YYYYMMDD/ for first non-empty basic_info
  2. Hardcoded minimal — for symbols that never succeeded (e.g., always-blocked
     endpoints), inject a tiny manually-curated dict with code/name/industry
     so downstream analyzers see at least identity + industry.

Usage:
    python scripts/enrich_basic_info.py                 # default: data/<today>
    python scripts/enrich_basic_info.py --date 20260514
    python scripts/enrich_basic_info.py --data-dir data/20260514

Output: writes each enriched snapshot in place, tagging
    fundamentals.basic_info_source = "historical_<src_date>_<original_source>"
                                  or "hardcoded_minimal"
and removing the resolved "basic_info" entry from errors.
"""
import argparse
import glob
import json
import os
import sys
from datetime import date


# Hardcoded minimal fallback for watchlist symbols that have NEVER had a
# successful basic_info fetch across all historical snapshots. Keep this list
# minimal (code/name/industry) — never claim live numbers.
HARDCODED_MINIMAL: dict[str, dict] = {
    "000988": {"股票代码": "000988", "股票简称": "华工科技",
               "行业": "通信设备"},
    "601869": {"股票代码": "601869", "股票简称": "长飞光纤",
               "行业": "通信设备"},
    "600522": {"股票代码": "600522", "股票简称": "中天科技",
               "行业": "通信设备"},
    "603256": {"股票代码": "603256", "股票简称": "宏和科技",
               "行业": "电子元件"},
    "603773": {"股票代码": "603773", "股票简称": "沃格光电",
               "行业": "光学光电子"},
    # Newly added 2026-05.
    "000021": {"股票代码": "000021", "股票简称": "深科技", "行业": "半导体"},
    "688627": {"股票代码": "688627", "股票简称": "精智达", "行业": "半导体"},
    "688206": {"股票代码": "688206", "股票简称": "概伦电子", "行业": "半导体"},
    "688521": {"股票代码": "688521", "股票简称": "芯原股份", "行业": "半导体"},
    "688047": {"股票代码": "688047", "股票简称": "龙芯中科", "行业": "半导体"},
    "600845": {"股票代码": "600845", "股票简称": "宝信软件", "行业": "软件开发"},
    "300499": {"股票代码": "300499", "股票简称": "高澜股份", "行业": "电源设备"},
    "002837": {"股票代码": "002837", "股票简称": "英维克", "行业": "电源设备"},
    "002156": {"股票代码": "002156", "股票简称": "通富微电", "行业": "半导体"},
    "600584": {"股票代码": "600584", "股票简称": "长电科技", "行业": "半导体"},
    "688981": {"股票代码": "688981", "股票简称": "中芯国际", "行业": "半导体"},
    "688347": {"股票代码": "688347", "股票简称": "华虹公司", "行业": "半导体"},
    "601138": {"股票代码": "601138", "股票简称": "工业富联", "行业": "通信设备"},
}

BASIC_INFO_DROP_KEYS = {"上市时间"}


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def prune_basic_info(info):
    if not isinstance(info, dict):
        return info
    return {k: v for k, v in info.items() if k not in BASIC_INFO_DROP_KEYS}


def find_historical(symbol, repo_root, current_date_tag):
    """Walk data/YYYYMMDD/ newest-to-oldest, return first non-empty basic_info."""
    data_root = os.path.join(repo_root, "data")
    if not os.path.isdir(data_root):
        return None, None
    dirs = sorted(
        (d for d in os.listdir(data_root) if d.isdigit() and len(d) == 8),
        reverse=True,
    )
    for date_tag in dirs:
        if date_tag == current_date_tag:
            continue
        snap = os.path.join(data_root, date_tag, f"{symbol}_snapshot.json")
        if not os.path.exists(snap):
            continue
        try:
            d = load_json(snap)
        except Exception:
            continue
        bi = d.get("fundamentals", {}).get("basic_info")
        if bi and len(bi) > 0:
            src = d.get("fundamentals", {}).get("basic_info_source", "em")
            return prune_basic_info(bi), f"historical_{date_tag}_{src}"
    return None, None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", help="Target dir (e.g. data/20260514)")
    ap.add_argument("--date", help="YYYYMMDD; resolves to data/<date>")
    ap.add_argument("--repo-root", help="Repo root (default: parent of scripts/)")
    args = ap.parse_args()

    repo_root = args.repo_root or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )

    if args.data_dir:
        target_dir = os.path.abspath(args.data_dir)
    elif args.date:
        target_dir = os.path.join(repo_root, "data", args.date)
    else:
        target_dir = os.path.join(repo_root, "data", date.today().strftime("%Y%m%d"))

    if not os.path.isdir(target_dir):
        print(f"ERROR: target dir not found: {target_dir}", file=sys.stderr)
        return 2

    current_tag = os.path.basename(target_dir)
    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))

    historical = hardcoded = skipped = failed = 0
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        try:
            d = load_json(snap)
        except Exception as e:
            print(f"  {symbol}: parse error: {e}", file=sys.stderr)
            failed += 1
            continue

        fund = d.setdefault("fundamentals", {})
        existing = fund.get("basic_info")
        if existing and len(existing) > 0:
            print(
                f"  {symbol}: skip (already has basic_info, "
                f"source={fund.get('basic_info_source')})"
            )
            skipped += 1
            continue

        # Stage 1: historical
        bi, source = find_historical(symbol, repo_root, current_tag)
        if bi is None and symbol in HARDCODED_MINIMAL:
            # Stage 2: hardcoded minimal
            bi = dict(HARDCODED_MINIMAL[symbol])  # copy to avoid mutating constant
            source = "hardcoded_minimal"

        if bi is None:
            print(f"  {symbol}: FAIL (no historical + no hardcoded entry)")
            failed += 1
            continue

        fund["basic_info"] = prune_basic_info(bi)
        fund["basic_info_source"] = source
        errs = fund.get("errors", {})
        errs.pop("basic_info", None)

        save_json(snap, d)
        if source.startswith("historical"):
            print(f"  {symbol}: enriched <- {source}")
            historical += 1
        else:
            print(f"  {symbol}: enriched <- {source}")
            hardcoded += 1

    print(
        f"\nDone: historical={historical}, hardcoded={hardcoded}, "
        f"skipped={skipped}, failed={failed}, total={len(snapshots)}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
