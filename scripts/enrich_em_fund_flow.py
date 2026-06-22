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
import subprocess
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


def fetch_historical_record(stock, symbol: str, target_iso: str) -> tuple[dict | None, str | None]:
    market_id = "1" if symbol.startswith(("5", "6", "9")) else "0"
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        f"?lmt=20&klt=101&secid={market_id}.{symbol}"
        "&fields1=f1,f2,f3,f7"
        "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
        "&ut=b2884a393a59ad64002292a3e90d46a5"
    )
    cmd = [
        "curl", "-fsS", "--retry", "3", "--retry-all-errors",
        "--connect-timeout", "5", "--max-time", "12", url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45, check=True)
        payload = json.loads(result.stdout)
        lines = (payload.get("data") or {}).get("klines") or []
    except Exception as exc:
        return None, f"historical endpoint failed: {type(exc).__name__}: {exc}"

    line = next((item for item in reversed(lines) if item.startswith(target_iso + ",")), None)
    if line is None:
        return None, f"no historical fund-flow row for {target_iso}"
    values = line.split(",")
    if len(values) < 13:
        return None, f"malformed historical fund-flow row for {target_iso}"

    def num(index: int) -> float | None:
        try:
            return float(values[index])
        except (TypeError, ValueError):
            return None

    rec = {
        "日期": target_iso,
        "收盘价": num(11),
        "涨跌幅": num(12),
        "主力净流入-净额": num(1),
        "主力净流入-净占比": num(6),
        "超大单净流入-净额": num(5),
        "超大单净流入-净占比": num(10),
        "大单净流入-净额": num(4),
        "大单净流入-净占比": num(9),
        "中单净流入-净额": num(3),
        "中单净流入-净占比": num(8),
        "小单净流入-净额": num(2),
        "小单净流入-净占比": num(7),
    }
    rec["股票代码"] = symbol
    rec["fund_flow_quality"] = "strong_em_order_split_historical"
    rec["fund_flow_note"] = "EastMoney历史资金接口；主力=超大单+大单"
    return rec, None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", help="Target dir")
    ap.add_argument("--date", help="YYYYMMDD")
    ap.add_argument("--repo-root", help="Repo root")
    ap.add_argument("--require", action="store_true", help="return non-zero if any symbol lacks EM strong flow")
    ap.add_argument("--force", action="store_true", help="replace an existing target-date row")
    ap.add_argument("--historical", action="store_true", help="skip today's rank and fetch exact historical rows")
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
    symbols = [snap.name[:6] for snap in snapshots]
    if args.historical:
        batch_records, batch_error = {}, "historical mode"
    else:
        batch_records, batch_error = stock.fetch_em_ulist_fund_flow_today(symbols, as_of=target_iso)
    batch_dates = {
        str(rec.get("em_update_time") or "")[:10]
        for rec in batch_records.values()
        if rec.get("em_update_time")
    }
    batch_matches_target = bool(batch_records) and batch_dates == {target_iso}
    if batch_records:
        print(
            f"EM ulist batch: loaded {len(batch_records)}/{len(symbols)} strong-flow records "
            f"update_dates={sorted(batch_dates) or ['unknown']}"
        )
        if not batch_matches_target:
            print(f"EM ulist batch is not for {target_iso}; using exact historical rows")
    elif batch_error:
        print(f"EM ulist batch unavailable: {batch_error}")

    for snap in snapshots:
        symbol = snap.name[:6]
        try:
            data = load_json(snap)
        except Exception as exc:
            print(f"  {symbol}: parse error: {exc}", file=sys.stderr)
            failed += 1
            continue

        sent = data.setdefault("sentiment", {})
        if not args.force and sent.get("fund_flow_source") in {"em_individual", "em_rank_today_order_split"}:
            rows = sent.get("fund_flow_recent_20d") or []
            if any(str(r.get("日期", ""))[:10] == target_iso for r in rows):
                skipped += 1
                continue

        rec = batch_records.get(symbol) if batch_matches_target else None
        err = batch_error if rec is None else None
        source = "em_rank_today_order_split"
        if rec is None and args.historical:
            rec, err = fetch_historical_record(stock, symbol, target_iso)
            source = "em_individual"
        elif rec is None and not err:
            err = f"same-day rank is not for {target_iso}"
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
        sent["fund_flow_source"] = source
        sent["fund_flow_quality"] = rec.get("fund_flow_quality", "strong_em_order_split_today_only")
        sent.pop("fund_flow_fallback_note", None)
        errs = sent.get("errors")
        if isinstance(errs, dict):
            for key in ("fund_flow", "fund_flow_fallback", "fund_flow_fallback_ths", "fund_flow_fallback_em_rank"):
                errs.pop(key, None)
        save_json(snap, data)
        print(f"  {symbol}: upgraded <- {source}")
        patched += 1

    print(f"\nDone: patched={patched}, skipped={skipped}, failed={failed}, total={len(snapshots)}")
    if failed and first_error:
        print(f"first_error: {first_error}")
    if args.require and failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
