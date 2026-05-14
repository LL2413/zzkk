#!/usr/bin/env python3
"""Backfill missing basic_info in today's snapshots from historical ones.

basic_info (公司简介/行业/股本等) is largely time-invariant. When EM/Xueqiu
endpoints are unreachable on a given fetch, we can still surface the same
descriptive metadata by lifting it from the most recent successful snapshot.

Usage:
    python scripts/enrich_basic_info.py                 # default: data/<today>
    python scripts/enrich_basic_info.py --date 20260514
    python scripts/enrich_basic_info.py --data-dir data/20260514

Output: writes each enriched snapshot in place, tagging
    fundamentals.basic_info_source = "historical_<src_date>_<original_source>"
and removing the resolved "basic_info" entry from errors.
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
            return bi, f"historical_{date_tag}_{src}"
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

    enriched = skipped = failed = 0
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

        bi, source = find_historical(symbol, repo_root, current_tag)
        if bi is None:
            print(f"  {symbol}: FAIL (no historical basic_info in any past snapshot)")
            failed += 1
            continue

        fund["basic_info"] = bi
        fund["basic_info_source"] = source
        # Clear resolved error
        errs = fund.get("errors", {})
        errs.pop("basic_info", None)

        save_json(snap, d)
        print(f"  {symbol}: enriched <- {source}")
        enriched += 1

    print(
        f"\nDone: enriched={enriched}, skipped={skipped}, "
        f"failed={failed}, total={len(snapshots)}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
