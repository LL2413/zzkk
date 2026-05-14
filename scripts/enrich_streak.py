#!/usr/bin/env python3
"""Compute consecutive-day main-fund-flow streak for each watchlist stock.

A "streak" is the length of the run of same-sign 主力净占比 ending on the
target date. Useful for spotting:
  - "Continuous accumulation" (e.g. 中科蓝讯 +4 days positive)
  - "Continuous distribution" (e.g. 亨通 -4 days negative)
  - Reversals (streak just broken)

We use 主力净占比 (signed percentage) rather than 主力净流入-净额 (signed yuan)
to normalize across symbols of different float sizes. Sign is determined by
strict positive / negative threshold (zero treated as "flat", breaks streak).

Output: adds `_main_flow_streak` to sentiment:
    {
        "direction": "positive" | "negative" | "flat",
        "days": <int>,
        "as_of": "<YYYY-MM-DD>",
        "main_pct_today": <number>,
        "main_yi_total_streak": <number>      # sum 主力净流入 over the streak
    }

Usage:
    python scripts/enrich_streak.py
    python scripts/enrich_streak.py --date 20260514
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


def compute_streak(fund_flow_list, target_iso):
    """Return dict describing the streak ending on target_iso, or None."""
    fmap = {str(f.get("日期", ""))[:10]: f for f in fund_flow_list if f.get("日期")}
    if target_iso not in fmap:
        return None

    # Sort entries by date ascending, then find target index
    sorted_dates = sorted(fmap.keys())
    try:
        idx = sorted_dates.index(target_iso)
    except ValueError:
        return None

    today_pct = fmap[target_iso].get("主力净流入-净占比")
    if today_pct is None:
        return None
    if today_pct > 0:
        direction = "positive"
    elif today_pct < 0:
        direction = "negative"
    else:
        return {
            "direction": "flat",
            "days": 1,
            "as_of": target_iso,
            "main_pct_today": 0,
            "main_yi_total_streak": 0,
        }

    streak_days = 0
    total_yi = 0.0
    for i in range(idx, -1, -1):
        f = fmap.get(sorted_dates[i], {})
        pct = f.get("主力净流入-净占比")
        if pct is None:
            break
        if (direction == "positive" and pct > 0) or (direction == "negative" and pct < 0):
            streak_days += 1
            total_yi += (f.get("主力净流入-净额") or 0) / 1e8
        else:
            break

    return {
        "direction": direction,
        "days": streak_days,
        "as_of": target_iso,
        "main_pct_today": round(today_pct, 4),
        "main_yi_total_streak": round(total_yi, 4),
    }


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
        ff = d.get("sentiment", {}).get("fund_flow_recent_20d") or []
        streak = compute_streak(ff, target_iso)
        if streak is None:
            print(f"  {symbol}: skip (no flow data for {target_iso})")
            skipped += 1
            continue
        d.setdefault("sentiment", {})["_main_flow_streak"] = streak
        save_json(snap, d)
        sign = "+" if streak["direction"] == "positive" else (
            "-" if streak["direction"] == "negative" else "0"
        )
        print(
            f"  {symbol}: streak {sign}{streak['days']}d "
            f"(today {streak['main_pct_today']:+.2f}%, "
            f"sum {streak['main_yi_total_streak']:+.2f}亿)"
        )
        computed += 1

    print(f"\nDone: computed={computed}, skipped={skipped}, total={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
