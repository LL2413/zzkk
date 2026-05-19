#!/usr/bin/env python3
"""Compute a 3-dimension signal score for each watchlist snapshot.

Combines three already-enriched fields into one composite "signal score":
  1. streak     (_main_flow_streak)   — main-fund direction persistence
  2. label      (_divergence)         — price-vs-flow quality
  3. valuation  (_valuation_summary)  — PE/PB cheapness

This is a SIGNAL-QUALITY score, NOT investment advice. It measures whether
main fund is accumulating, whether the price move is "real", and whether
valuation is stretched — nothing about future returns.

MUST run AFTER enrich_streak / enrich_divergence / enrich_valuation.

Scoring (weights are deliberate — streak & label dominate, valuation minor):
  streak:    +2 (>=3d in) / +1 (1-2d in) / 0 (flat) / -1 (1-2d out) / -2 (>=3d out)
  label:     +1 (均衡/吸筹) / 0 (漂移) / -2 (拥挤/恐慌)
  valuation: +1 (PE<40) / 0 (PE 40-100) / -1 (PE>100 or PB-only/loss)

tier: total>=2 偏多 | 0<=total<=1 中性 | total<0 警示

Output: adds `_signal_score` to the snapshot root.

Usage:
    python scripts/enrich_score.py
    python scripts/enrich_score.py --date 20260519
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


def score_streak(streak):
    d = streak.get("direction")
    days = streak.get("days") or 0
    if d == "positive":
        return 2 if days >= 3 else 1
    if d == "negative":
        return -2 if days >= 3 else -1
    return 0


def score_label(divergence):
    return {"均衡": 1, "吸筹": 1, "漂移": 0, "拥挤": -2, "恐慌": -2}.get(
        divergence.get("interpretation", ""), 0
    )


def score_valuation(valuation):
    metric = valuation.get("primary_metric", "")
    value = valuation.get("primary_value")
    if metric == "PB":
        return -1            # PE invalid => loss-making / extreme
    if value is None:
        return 0
    if value < 40:
        return 1
    if value <= 100:
        return 0
    return -1


def light(score):
    return "🟢" if score > 0 else ("🔴" if score < 0 else "🟡")


def tier(total):
    if total >= 2:
        return "偏多"
    if total >= 0:
        return "中性"
    return "警示"


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
    results = []
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        d = load_json(snap)
        streak = d.get("sentiment", {}).get("_main_flow_streak")
        divergence = d.get("sentiment", {}).get("_divergence")
        valuation = d.get("fundamentals", {}).get("_valuation_summary")
        if streak is None or divergence is None or valuation is None:
            print(f"  {symbol}: skip (missing streak/divergence/valuation — run those first)")
            skipped += 1
            continue

        s1 = score_streak(streak)
        s2 = score_label(divergence)
        s3 = score_valuation(valuation)
        total = s1 + s2 + s3
        d["_signal_score"] = {
            "as_of": target_iso,
            "streak_score": s1,
            "label_score": s2,
            "valuation_score": s3,
            "total": total,
            "tier": tier(total),
            "lights": {"streak": light(s1), "label": light(s2), "valuation": light(s3)},
            "note": "信号质量评分，非投资建议；streak/label/valuation 三维合成",
        }
        save_json(snap, d)
        results.append((total, symbol, d["_signal_score"]["tier"]))
        computed += 1

    results.sort(key=lambda x: -x[0])
    print(f"\n=== signal scores for {target_iso} ===")
    for total, symbol, t in results:
        print(f"  {symbol}  total={total:>+3}  [{t}]")
    print(f"\nDone: computed={computed}, skipped={skipped}, total={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
