#!/usr/bin/env python3
"""Backfill same-day fund_flow_recent_20d from THS when EM fund-flow failed.

This is intentionally an enricher rather than a replacement fetcher. EastMoney
is still the preferred source because it exposes super+large-order "main"
money-flow history. When EM push2/push2his is rate-limited, THS gives a weaker
but usable same-day net-flow proxy so watchlist scans, sector aggregation,
streak, divergence, alpha, and score do not all collapse to empty.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from datetime import date
from pathlib import Path


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def load_stock_module(repo_root: Path):
    stock_path = repo_root / "scripts" / "stock.py"
    spec = importlib.util.spec_from_file_location("stock_fetcher", stock_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {stock_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def has_target_flow(rows: list[dict], target_iso: str) -> bool:
    for row in rows:
        if str(row.get("日期", ""))[:10] == target_iso and row.get("主力净流入-净额") is not None:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", help="Target dir")
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--repo-root", help="Repo root")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path(__file__).resolve().parents[1]
    if args.data_dir:
        target_dir = Path(args.data_dir).resolve()
    elif args.date:
        target_dir = repo_root / "data" / args.date
    else:
        target_dir = repo_root / "data" / date.today().strftime("%Y%m%d")

    if not target_dir.is_dir():
        print(f"ERROR: target dir not found: {target_dir}", file=sys.stderr)
        return 2

    current_tag = target_dir.name
    target_iso = f"{current_tag[:4]}-{current_tag[4:6]}-{current_tag[6:8]}"
    today_iso = date.today().strftime("%Y-%m-%d")
    if target_iso != today_iso:
        data_root = repo_root / "data"
        latest_tag = max(
            (p.name for p in data_root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8),
            default=current_tag,
        )
        if current_tag != latest_tag:
            print(f"skip: THS fund-flow fallback is latest-day only ({current_tag} != {latest_tag})")
            return 0
        print(f"note: using latest THS table for latest data dir {current_tag} after local date rolled to {today_iso}")

    stock = load_stock_module(repo_root)
    snapshots = sorted(target_dir.glob("*_snapshot.json"))

    patched = skipped = failed = 0
    for snap in snapshots:
        symbol = snap.name[:6]
        try:
            data = load_json(snap)
        except Exception as exc:
            print(f"  {symbol}: parse error: {exc}", file=sys.stderr)
            failed += 1
            continue

        sent = data.setdefault("sentiment", {})
        rows = sent.get("fund_flow_recent_20d") or []
        if isinstance(rows, list) and has_target_flow(rows, target_iso):
            skipped += 1
            continue

        rec, err = stock.fetch_ths_fund_flow_today(symbol, as_of=target_iso)
        if not rec:
            print(f"  {symbol}: FAIL {err}")
            failed += 1
            continue

        if isinstance(rows, list) and rows:
            rows = [r for r in rows if str(r.get("日期", ""))[:10] != target_iso]
            rows.append(rec)
            sent["fund_flow_recent_20d"] = rows
        else:
            sent["fund_flow_recent_20d"] = [rec]
        sent["fund_flow_source"] = "ths_individual_net_today_only"
        sent["fund_flow_fallback_note"] = "THS净额fallback；不是EastMoney超大单+大单主力口径"

        errs = sent.get("errors")
        if isinstance(errs, dict):
            errs.pop("fund_flow", None)
            errs.pop("fund_flow_fallback", None)
            errs.pop("fund_flow_fallback_ths", None)

        save_json(snap, data)
        print(f"  {symbol}: backfilled <- THS")
        patched += 1

    print(f"\nDone: patched={patched}, skipped={skipped}, failed={failed}, total={len(snapshots)}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
