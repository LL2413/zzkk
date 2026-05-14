#!/usr/bin/env python3
"""Score the divergence between price move and main-fund-flow share.

divergence = 涨跌幅 - 主力净占比 (both in percent, signed).

Interpretation:
  large positive (>+10): price rose much more than main flow share supports,
      OR price fell less than main outflow share — typical "散户/游资推动"
      or "拥挤交易" or "诱多" pattern. Most useful at high prior cumulative
      returns (the澜起5-11 / 光迅 连板 archetype).
  small absolute (|x| <= 3): healthy alignment.
  large negative (<-10): price moved up less than main inflow share (吸筹
      pattern, low-key accumulation) OR price fell more than main outflow
      supports (selling exceeded by retail panic).

We compute it for the target date and emit:
    sentiment._divergence = {
        "as_of": "<YYYY-MM-DD>",
        "chg_pct": <number>,
        "main_pct": <number>,
        "score": chg_pct - main_pct,
        "interpretation": "crowded" | "balanced" | "accumulation" | "panic"
    }

Usage:
    python scripts/enrich_divergence.py
    python scripts/enrich_divergence.py --date 20260514
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


def classify(score, chg_pct, main_pct):
    """Bucket the divergence into a short label."""
    if abs(score) <= 3:
        return "balanced"
    if score > 10:
        # Price moved up far more than main inflow, OR price fell less than
        # main outflow — both flavors of "price decoupled from main fund"
        return "crowded"  # warning: 散户/游资推动 or 主力出货价不跌
    if score < -10:
        # Main inflow > price rise (accumulation), or price fell more than
        # main outflow supports (panic selling beyond institutional view)
        if main_pct > 0:
            return "accumulation"
        return "panic"
    return "drift"


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
        today = next((f for f in ff if str(f.get("日期", ""))[:10] == target_iso), None)
        if not today:
            print(f"  {symbol}: skip (no flow row for {target_iso})")
            skipped += 1
            continue
        chg = today.get("涨跌幅")
        main_pct = today.get("主力净流入-净占比")
        if chg is None or main_pct is None:
            print(f"  {symbol}: skip (missing 涨跌幅 or 主力净占比)")
            skipped += 1
            continue
        score = chg - main_pct
        label = classify(score, chg, main_pct)
        d.setdefault("sentiment", {})["_divergence"] = {
            "as_of": target_iso,
            "chg_pct": round(chg, 4),
            "main_pct": round(main_pct, 4),
            "score": round(score, 4),
            "interpretation": label,
        }
        save_json(snap, d)
        print(
            f"  {symbol}: chg {chg:+6.2f}% - main {main_pct:+6.2f}% = "
            f"score {score:+6.2f}  [{label}]"
        )
        computed += 1

    print(f"\nDone: computed={computed}, skipped={skipped}, total={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
