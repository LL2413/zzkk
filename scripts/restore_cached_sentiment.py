#!/usr/bin/env python3
"""Restore raw per-day sentiment sections from the stock cache."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", help="YYYYMMDD")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    root = args.repo_root.resolve()
    data_dir = root / "data" / args.date
    cache_dir = root / ".cache" / "stock"
    restored = missing = 0

    for snapshot_path in sorted(data_dir.glob("*_snapshot.json")):
        symbol = snapshot_path.name[:6]
        cache_path = cache_dir / f"{symbol}_sentiment_{args.date}.json"
        if not cache_path.exists():
            print(f"  {symbol}: missing cache")
            missing += 1
            continue
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
        sentiment = json.loads(cache_path.read_text(encoding="utf-8-sig"))
        snapshot["sentiment"] = sentiment
        snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"  {symbol}: sentiment restored <- {cache_path.name}")
        restored += 1

    print(f"\nDone: restored={restored}, missing={missing}, total={restored + missing}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
