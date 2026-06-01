#!/usr/bin/env python3
"""Fill missing daily valuation rows from the latest historical snapshot.

Daily fast refresh intentionally skips the slow stock_value_em endpoint. For
stocks without a fresh valuation row, update the most recent historical row by
the current close-price ratio. The result is explicitly tagged as derived so a
report never presents it as a live endpoint response.
"""
import argparse
import copy
import glob
import json
import os
import sys
from datetime import date


SCALED_KEYS = (
    "总市值",
    "流通市值",
    "PE(TTM)",
    "PE(静)",
    "市净率",
    "PEG值",
    "市现率",
    "市销率",
)


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def row_date(row):
    return str(row.get("date", row.get("日期", "")))[:10]


def row_close(row):
    return number(row.get("close", row.get("收盘")))


def target_close_and_chg(snapshot, target_iso):
    rows = snapshot.get("sentiment", {}).get("price_recent") or []
    prices = [(row_date(row), row_close(row)) for row in rows if isinstance(row, dict)]
    prices = [(day, close) for day, close in prices if day and close is not None]
    for index, (day, close) in enumerate(prices):
        if day != target_iso:
            continue
        if index > 0 and prices[index - 1][1]:
            return close, (close / prices[index - 1][1] - 1) * 100
        return close, None
    return None, None


def valuation_row_date(row):
    return str(row.get("数据日期", row.get("date", "")))[:10]


def latest_historical_valuation(repo_root, symbol, current_tag):
    data_root = os.path.join(repo_root, "data")
    tags = sorted(
        (
            tag for tag in os.listdir(data_root)
            if tag.isdigit() and len(tag) == 8 and tag < current_tag
        ),
        reverse=True,
    )
    for tag in tags:
        path = os.path.join(data_root, tag, f"{symbol}_snapshot.json")
        if not os.path.exists(path):
            continue
        try:
            snapshot = load_json(path)
        except Exception:
            continue
        fund = snapshot.get("fundamentals", {})
        latest = fund.get("valuation_latest") or []
        source = str(fund.get("valuation_source", ""))
        if latest and not source.startswith("derived_price_adjusted_from_"):
            return tag, fund
    return None, None


def derive_row(previous_row, target_iso, close, chg_pct):
    previous_close = number(previous_row.get("当日收盘价"))
    if previous_close is None or previous_close <= 0:
        return None
    ratio = close / previous_close
    row = copy.deepcopy(previous_row)
    row["数据日期"] = f"{target_iso}T00:00:00.000"
    row["当日收盘价"] = close
    if chg_pct is not None:
        row["当日涨跌幅"] = chg_pct
    for key in SCALED_KEYS:
        value = number(previous_row.get(key))
        if value is not None:
            row[key] = value * ratio
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
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
    fresh = derived = failed = sector_missing = 0
    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))

    for path in snapshots:
        symbol = os.path.basename(path)[:6]
        snapshot = load_json(path)
        fund = snapshot.setdefault("fundamentals", {})
        sector = snapshot.setdefault("sector", {})
        sector_errors = sector.setdefault("errors", {})
        if sector.get("sector_price_recent"):
            sector_errors.pop("sector_price_recent", None)
        else:
            sector_errors["sector_price_recent"] = (
                "missing daily sector price; fast refresh does not fabricate a fallback"
            )
            sector_missing += 1

        latest = fund.get("valuation_latest") or []
        valuation_source = str(fund.get("valuation_source", ""))
        latest_is_today = latest and valuation_row_date(latest[0]) == target_iso
        if latest_is_today and valuation_source.startswith("derived_price_adjusted_from_"):
            derived += 1
            save_json(path, snapshot)
            print(f"  {symbol}: derived valuation kept")
            continue
        if latest_is_today and valuation_source != "skipped_fast_daily":
            fund.setdefault("errors", {}).pop("valuation_price_adjusted_fallback", None)
            fresh += 1
            save_json(path, snapshot)
            print(f"  {symbol}: fresh valuation kept")
            continue

        close, chg_pct = target_close_and_chg(snapshot, target_iso)
        source_tag, previous_fund = latest_historical_valuation(
            repo_root, symbol, current_tag
        )
        if close is None or not previous_fund:
            print(f"  {symbol}: FAIL (no current close or historical valuation)")
            failed += 1
            save_json(path, snapshot)
            continue

        previous_latest = previous_fund.get("valuation_latest") or []
        row = derive_row(previous_latest[0], target_iso, close, chg_pct)
        if row is None:
            print(f"  {symbol}: FAIL (historical valuation has no valid close)")
            failed += 1
            save_json(path, snapshot)
            continue

        recent = copy.deepcopy(previous_fund.get("valuation_recent") or [])
        recent = [
            existing for existing in recent
            if str(existing.get("数据日期", ""))[:10] != target_iso
        ]
        recent.append(row)
        fund["valuation_latest"] = [row]
        fund["valuation_recent"] = recent[-20:]
        fund["valuation_source"] = f"derived_price_adjusted_from_{source_tag}"
        fund.setdefault("errors", {})["valuation_price_adjusted_fallback"] = (
            f"fresh daily valuation endpoint unavailable; price-adjusted from {source_tag}"
        )
        derived += 1
        save_json(path, snapshot)
        print(f"  {symbol}: derived valuation <- {source_tag}")

    print(
        f"\nDone: fresh={fresh}, derived={derived}, failed={failed}, "
        f"sector_price_missing={sector_missing}, total={len(snapshots)}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
