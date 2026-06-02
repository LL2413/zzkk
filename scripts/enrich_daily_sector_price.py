#!/usr/bin/env python3
"""Fill missing daily sector prices from cached Tencent ETF proxy klines.

Fast daily snapshots skip the slow per-stock sector endpoint. This enricher
requests each confirmed ETF proxy once, then shares the resulting kline with
all matching stocks. Proxy sources stay explicit so alpha is never presented
as a canonical industry-index comparison.
"""
import argparse
import glob
import json
import os
import socket
import sys
from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd


ETF_PROXY = {
    "半导体": "512760",
    "消费电子": "159732",
    "通信设备": "515880",
    "通信": "515880",
    "化学制品": "159870",
    "电子化学品Ⅱ": "159870",
    "电子化学品": "159870",
    "化工": "159870",
    "医疗服务": "159929",
    "医药生物": "159929",
    "计算机设备": "159998",
    "计算机应用": "159998",
    "计算机": "159998",
    "软件开发": "159998",
    "软件": "159998",
    "电子": "512760",
    "电子元件": "512760",
    "元件": "512760",
    "光学光电子": "512760",
    "航空装备Ⅱ": "512810",
    "航空装备": "512810",
    "航天航空": "512810",
    "国防军工": "512810",
}


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def row_date(row):
    return str(row.get("date", row.get("日期", "")))[:10]


def has_target_row(rows, target_iso):
    return any(row_date(row) == target_iso for row in rows if isinstance(row, dict))


def normalize_records(frame):
    records = []
    for row in frame.tail(20).to_dict(orient="records"):
        normalized = {}
        for key, value in row.items():
            if pd.isna(value):
                normalized[str(key)] = None
            elif hasattr(value, "item"):
                normalized[str(key)] = value.item()
            elif isinstance(value, (date, datetime)):
                normalized[str(key)] = str(value)[:10]
            else:
                normalized[str(key)] = value
        records.append(normalized)
    return records


def fetch_proxy(etf_code, start_tag, end_tag):
    symbol = ("sh" if etf_code.startswith(("5", "6")) else "sz") + etf_code
    last_error = None
    for _ in range(2):
        try:
            frame = ak.stock_zh_a_hist_tx(
                symbol=symbol, start_date=start_tag, end_date=end_tag, adjust="qfq"
            )
            if isinstance(frame, pd.DataFrame) and len(frame) > 0:
                return normalize_records(frame), None
            last_error = f"empty result for ETF {etf_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
    return [], last_error or f"no result for ETF {etf_code}"


def industry_name(sector):
    return (
        sector.get("industry_em")
        or sector.get("industry_xq")
        or sector.get("industry_hardcoded")
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", help="Target dir")
    ap.add_argument("--date", help="YYYYMMDD")
    args = ap.parse_args()

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    start_tag = (
        datetime.strptime(current_tag, "%Y%m%d").date() - timedelta(days=120)
    ).strftime("%Y%m%d")
    cache = {}
    kept = patched = missing = failed = 0
    socket.setdefaulttimeout(12)
    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))

    for path in snapshots:
        symbol = os.path.basename(path)[:6]
        snapshot = load_json(path)
        sector = snapshot.setdefault("sector", {})
        errors = sector.setdefault("errors", {})
        if has_target_row(sector.get("sector_price_recent") or [], target_iso):
            errors.pop("sector_price_recent", None)
            kept += 1
            save_json(path, snapshot)
            print(f"  {symbol}: existing daily sector price kept")
            continue

        industry = industry_name(sector)
        etf_code = ETF_PROXY.get(industry)
        if not etf_code:
            errors["sector_price_recent"] = (
                f"missing daily sector price; no confirmed ETF proxy for {industry or 'unknown'}"
            )
            missing += 1
            save_json(path, snapshot)
            print(f"  {symbol}: skip (no confirmed ETF proxy for {industry or 'unknown'})")
            continue

        if etf_code not in cache:
            cache[etf_code] = fetch_proxy(etf_code, start_tag, current_tag)
        rows, error = cache[etf_code]
        if error or not has_target_row(rows, target_iso):
            errors["sector_price_recent"] = (
                error or f"ETF {etf_code} has no row for {target_iso}"
            )
            failed += 1
            save_json(path, snapshot)
            print(f"  {symbol}: FAIL ({errors['sector_price_recent']})")
            continue

        sector["sector_price_recent"] = rows
        sector["sector_price_source"] = f"etf_tencent_{etf_code}_proxy_for_{industry}"
        sector["sector_price_proxy_code"] = etf_code
        errors.pop("sector_price_recent", None)
        patched += 1
        save_json(path, snapshot)
        print(f"  {symbol}: sector price <- Tencent ETF {etf_code} proxy")

    print(
        f"\nDone: kept={kept}, patched={patched}, missing={missing}, failed={failed}, "
        f"proxy_requests={len(cache)}, total={len(snapshots)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
