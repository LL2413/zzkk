#!/usr/bin/env python3
"""Summarize valuation into a primary metric, picking PB or PS when PE invalid.

Loss-making stocks have negative or NULL PE TTM; PE>1000 is also typically not
meaningful for analysis. In those cases the analyst should fall back to PB
(asset-based) or PS (revenue-based). Rather than make every downstream reader
re-implement this triage, we pre-compute a tagged summary.

Output: adds `_valuation_summary` block to fundamentals:
    {
        "primary_metric": "PE_TTM" | "PB" | "PS" | "N/A",
        "primary_value": <number or null>,
        "pe_ttm": <number or null>,
        "pb": <number or null>,
        "ps": <number or null>,
        "rule": "PE_TTM if 0<x<1000 else PB if >0 else PS if >0 else N/A"
    }

Skips snapshots where valuation_latest is missing entirely.

Usage:
    python scripts/enrich_valuation.py                # default: data/<today>
    python scripts/enrich_valuation.py --date 20260514
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


def pick_primary(pe, pb, ps):
    if pe is not None and 0 < pe < 1000:
        return "PE_TTM", pe
    if pb is not None and pb > 0:
        return "PB", pb
    if ps is not None and ps > 0:
        return "PS", ps
    return "N/A", None


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

    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))
    summarized = no_val = 0
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        d = load_json(snap)
        val_list = d.get("fundamentals", {}).get("valuation_latest") or []
        if not val_list:
            print(f"  {symbol}: skip (no valuation_latest)")
            no_val += 1
            continue
        v = val_list[0]
        pe = v.get("PE(TTM)")
        pb = v.get("市净率")
        ps = v.get("市销率")
        metric, value = pick_primary(pe, pb, ps)
        d["fundamentals"]["_valuation_summary"] = {
            "primary_metric": metric,
            "primary_value": value,
            "pe_ttm": pe,
            "pb": pb,
            "ps": ps,
            "rule": "PE_TTM if 0<x<1000 else PB if >0 else PS if >0 else N/A",
        }
        save_json(snap, d)
        if value is None:
            print(f"  {symbol}: primary=N/A (pe={pe}, pb={pb}, ps={ps})")
        else:
            print(f"  {symbol}: primary={metric}={value}")
        summarized += 1

    print(f"\nDone: summarized={summarized}, no_val={no_val}, total={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
