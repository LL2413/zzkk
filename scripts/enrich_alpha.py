#!/usr/bin/env python3
"""Compute stock alpha vs its sector for the target date.

alpha = stock_chg - sector_chg, in percentage points.

Positive alpha: stock outperformed its sector on the day
Negative alpha: stock underperformed (sector dragged it / stock-specific weakness)

We pull stock 涨跌幅 from fund_flow_recent_20d[target] and sector 涨跌幅 from
sector_price_recent: today_close / prev_close - 1.

Snapshots without sector classification (new stocks like 603256/603773 that
fall through both EM and Xueqiu industry lookup) get no alpha record — they
have industry_hardcoded but the sector_price_recent kline often fails too.

Output: sector._alpha_vs_sector = {
    "as_of": "<YYYY-MM-DD>",
    "stock_chg": <pct>,
    "sector_chg": <pct>,
    "alpha": <pct points>,
    "sector_price_source": "<from snapshot>"
}

Usage:
    python scripts/enrich_alpha.py
    python scripts/enrich_alpha.py --date 20260514
"""
import argparse
import glob
import json
import os
import sys
from datetime import date


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def get_stock_chg(snap_dict, target_iso):
    ff = snap_dict.get("sentiment", {}).get("fund_flow_recent_20d") or []
    for f in ff:
        if str(f.get("日期", ""))[:10] == target_iso:
            return f.get("涨跌幅")
    return None


def get_sector_chg(snap_dict, target_iso):
    """Return (sector_chg_pct, source) computed from sector_price_recent."""
    sp = snap_dict.get("sector", {}).get("sector_price_recent") or []
    if len(sp) < 2:
        return None, None
    # Sort by date ascending; find target's index, look back one
    sorted_sp = sorted(sp, key=lambda x: str(x.get("date", "")))
    for i, p in enumerate(sorted_sp):
        if str(p.get("date", ""))[:10] == target_iso and i > 0:
            today_close = p.get("close")
            prev_close = sorted_sp[i - 1].get("close")
            if today_close and prev_close and prev_close != 0:
                chg = (today_close / prev_close - 1) * 100
                src = snap_dict.get("sector", {}).get("sector_price_source")
                return chg, src
            return None, None
    return None, None


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", help="Target dir")
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--repo-root", help="Repo root")
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
    target_iso = f"{current_tag[:4]}-{current_tag[4:6]}-{current_tag[6:8]}"
    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))

    computed = skipped = 0
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        d = load_json(snap)
        stock_chg = get_stock_chg(d, target_iso)
        sector_chg, source = get_sector_chg(d, target_iso)
        if stock_chg is None or sector_chg is None:
            reason = []
            if stock_chg is None: reason.append("no stock chg")
            if sector_chg is None: reason.append("no sector chg")
            print(f"  {symbol}: skip ({', '.join(reason)})")
            skipped += 1
            continue
        alpha = stock_chg - sector_chg
        d.setdefault("sector", {})["_alpha_vs_sector"] = {
            "as_of": target_iso,
            "stock_chg": round(stock_chg, 4),
            "sector_chg": round(sector_chg, 4),
            "alpha": round(alpha, 4),
            "sector_price_source": source,
        }
        save_json(snap, d)
        print(
            f"  {symbol}: stock {stock_chg:+6.2f}% - sector {sector_chg:+6.2f}% "
            f"= alpha {alpha:+6.2f}"
        )
        computed += 1

    print(f"\nDone: computed={computed}, skipped={skipped}, total={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
