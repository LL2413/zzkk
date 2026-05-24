#!/usr/bin/env python3
"""Upgrade snapshots to EastMoney order-split fund-flow口径 when available.

This enricher is intentionally placed before the THS fallback. It tries to
patch the target day's `fund_flow_recent_20d` with EastMoney's strong same-day
rank data:

    主力 = 超大单 + 大单

If EastMoney is still blocked/rate-limited, it exits successfully by default so
the rest of the pipeline can continue with the weaker THS fallback. Pass
`--require` when a report must not be generated without strong EM口径.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
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


def replace_target_row(rows: list[dict], rec: dict, target_iso: str) -> list[dict]:
    kept = [r for r in rows if str(r.get("日期", ""))[:10] != target_iso]
    kept.append(rec)
    return sorted(kept, key=lambda r: str(r.get("日期", ""))[:10])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", help="Target dir")
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--repo-root", help="Repo root")
    ap.add_argument("--require", action="store_true", help="return non-zero if any symbol lacks EM strong flow")
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
    stock = load_stock_module(repo_root)

    patched = skipped = failed = 0
    first_error = None
    snapshots = sorted(target_dir.glob("*_snapshot.json"))
    for snap in snapshots:
        symbol = snap.name[:6]
        try:
            data = load_json(snap)
        except Exception as exc:
            print(f"  {symbol}: parse error: {exc}", file=sys.stderr)
            failed += 1
            continue

        sent = data.setdefault("sentiment", {})
        if sent.get("fund_flow_source") in {"em_individual", "em_rank_today_order_split"}:
            rows = sent.get("fund_flow_recent_20d") or []
            if any(str(r.get("日期", ""))[:10] == target_iso for r in rows):
                skipped += 1
                continue

        rec, err = stock.fetch_em_rank_fund_flow_today(symbol, as_of=target_iso)
        if not rec:
            if first_error is None and err:
                first_error = err
            print(f"  {symbol}: no EM order-split flow ({err})")
            failed += 1
            continue

        rows = sent.get("fund_flow_recent_20d") or []
        if not isinstance(rows, list):
            rows = []
        sent["fund_flow_recent_20d"] = replace_target_row(rows, rec, target_iso)
        sent["fund_flow_source"] = "em_rank_today_order_split"
        sent["fund_flow_quality"] = "strong_em_order_split_today_only"
        sent.pop("fund_flow_fallback_note", None)
        errs = sent.get("errors")
        if isinstance(errs, dict):
            for key in ("fund_flow", "fund_flow_fallback", "fund_flow_fallback_ths", "fund_flow_fallback_em_rank"):
                errs.pop(key, None)
        save_json(snap, data)
        print(f"  {symbol}: upgraded <- EM order-split rank")
        patched += 1

    print(f"\nDone: patched={patched}, skipped={skipped}, failed={failed}, total={len(snapshots)}")
    if failed and first_error:
        print(f"first_error: {first_error}")
    if args.require and failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
