#!/usr/bin/env python3
"""Backfill one symbol into historical daily data directories.

This is intentionally symbol-scoped: it creates/updates
data/YYYYMMDD/<symbol>_snapshot.json for trading days in the requested range
and writes a compact merged history under data/history/.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("symbol", help="6-digit A-share code")
    ap.add_argument("--name", required=True, help="Chinese stock name")
    ap.add_argument("--industry", required=True, help="Project industry label")
    ap.add_argument("--start", required=True, help="YYYYMMDD")
    ap.add_argument("--end", required=True, help="YYYYMMDD")
    ap.add_argument("--repo-root", type=Path, default=ROOT)
    return ap.parse_args()


def market_prefix(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return "sh"
    if symbol.startswith(("0", "3")):
        return "sz"
    if symbol.startswith(("4", "8")):
        return "bj"
    raise ValueError(f"unsupported A-share symbol: {symbol}")


def eastmoney_secid(symbol: str) -> str:
    market = "1" if market_prefix(symbol) == "sh" else "0"
    return f"{market}.{symbol}"


def iso_date(tag: str) -> str:
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"


def tag_date(text: Any) -> str:
    return str(text or "")[:10].replace("-", "")


def json_records(frame) -> list[dict[str, Any]]:
    records = []
    for row in frame.to_dict(orient="records"):
        out = {}
        for key, value in row.items():
            if hasattr(value, "item"):
                value = value.item()
            if str(value) == "nan":
                value = None
            out[str(key)] = value
        records.append(out)
    return records


def load_stock_module(end_tag: str):
    os.environ.setdefault("NO_PROXY", "*")
    os.environ.setdefault("no_proxy", "*")
    os.environ.setdefault("STOCK_DATE_TAG", end_tag)
    os.environ.setdefault("STOCK_FINANCIALS_MODE", "refresh")
    sys.path.insert(0, str(ROOT / "scripts"))
    import stock  # type: ignore

    return stock


def fetch_price_history(ak, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    tx_symbol = market_prefix(symbol) + symbol
    frame = ak.stock_zh_a_hist_tx(
        symbol=tx_symbol, start_date=start, end_date=end, adjust="qfq"
    )
    if frame is None or len(frame) == 0:
        return []
    return json_records(frame)


def fetch_valuation_history(ak, symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    frame = ak.stock_value_em(symbol=symbol)
    if frame is None or len(frame) == 0:
        return []
    rows = json_records(frame)
    return [row for row in rows if start <= tag_date(row.get("数据日期")) <= end]


def parse_jsonp(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("{"):
        return json.loads(text)
    match = re.search(r"^[^(]*\((.*)\)\s*;?$", text, re.S)
    if not match:
        raise ValueError("response is not JSON/JSONP")
    return json.loads(match.group(1))


def fetch_fund_flow_history(symbol: str) -> list[dict[str, Any]]:
    import requests

    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": eastmoney_secid(symbol),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "cb": "jQuery",
        "_": int(time.time() * 1000),
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    }
    session = requests.Session()
    session.trust_env = False
    response = session.get(url, params=params, headers=headers, timeout=20)
    response.raise_for_status()
    payload = parse_jsonp(response.text)
    klines = (payload.get("data") or {}).get("klines") or []
    rows: list[dict[str, Any]] = []
    for item in klines:
        parts = str(item).split(",")
        if len(parts) < 13:
            continue
        date_s = parts[0]
        rows.append({
            "日期": date_s,
            "股票代码": symbol,
            "收盘价": float(parts[11]) if parts[11] else None,
            "涨跌幅": float(parts[12]) if parts[12] else None,
            "主力净流入-净额": float(parts[1]) if parts[1] else None,
            "主力净流入-净占比": float(parts[6]) if parts[6] else None,
            "超大单净流入-净额": float(parts[5]) if parts[5] else None,
            "超大单净流入-净占比": float(parts[10]) if parts[10] else None,
            "大单净流入-净额": float(parts[4]) if parts[4] else None,
            "大单净流入-净占比": float(parts[9]) if parts[9] else None,
            "中单净流入-净额": float(parts[3]) if parts[3] else None,
            "中单净流入-净占比": float(parts[8]) if parts[8] else None,
            "小单净流入-净额": float(parts[2]) if parts[2] else None,
            "小单净流入-净占比": float(parts[7]) if parts[7] else None,
            "fund_flow_quality": "strong_em_order_split_history",
            "fund_flow_note": "EastMoney push2his 个股历史分档资金；主力=超大单+大单",
        })
    return rows


def date_range(start_tag: str, end_tag: str) -> list[str]:
    start = datetime.strptime(start_tag, "%Y%m%d").date()
    end = datetime.strptime(end_tag, "%Y%m%d").date()
    days = []
    cur = start
    while cur <= end:
        days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days


def set_dates(obj: Any, target_iso: str) -> Any:
    if isinstance(obj, dict):
        out = copy.deepcopy(obj)
        if "as_of" in out:
            out["as_of"] = datetime.now().isoformat(timespec="seconds")
        if "data_date" in out:
            out["data_date"] = target_iso
        return out
    return copy.deepcopy(obj)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    os.chdir(repo_root)

    stock = load_stock_module(args.end)
    ak = stock.ak

    symbol = args.symbol
    basic_info = {"股票代码": symbol, "股票简称": args.name, "行业": args.industry}

    fundamentals = stock.fetch_fundamentals(symbol, _force=True)
    fundamentals["basic_info"] = basic_info
    fundamentals["basic_info_source"] = "hardcoded_minimal"
    fundamentals.setdefault("errors", {}).pop("basic_info", None)

    price_rows = fetch_price_history(ak, symbol, args.start, args.end)
    valuation_rows = fetch_valuation_history(ak, symbol, args.start, args.end)
    fund_rows = [
        row for row in fetch_fund_flow_history(symbol)
        if args.start <= tag_date(row.get("日期")) <= args.end
    ]

    price_by_date = {tag_date(row.get("date")): row for row in price_rows}
    valuation_by_date = {tag_date(row.get("数据日期")): row for row in valuation_rows}
    fund_by_date = {tag_date(row.get("日期")): row for row in fund_rows}
    trading_tags = sorted(price_by_date)

    written = 0
    missing_flow = []
    for target_tag in trading_tags:
        target_iso = iso_date(target_tag)
        out_dir = repo_root / "data" / target_tag
        out_dir.mkdir(parents=True, exist_ok=True)

        price_recent = [
            row for row in price_rows if tag_date(row.get("date")) <= target_tag
        ][-20:]
        valuation_recent = [
            row for row in valuation_rows if tag_date(row.get("数据日期")) <= target_tag
        ][-20:]
        fund_recent = [
            {**row, "股票简称": args.name}
            for row in fund_rows if tag_date(row.get("日期")) <= target_tag
        ][-20:]

        fund_part = set_dates(fundamentals, target_iso)
        fund_part["valuation_recent"] = valuation_recent
        if target_tag in valuation_by_date:
            fund_part["valuation_latest"] = [valuation_by_date[target_tag]]
            fund_part["valuation_source"] = "em_stock_value_history"
            fund_part.setdefault("errors", {}).pop("valuation", None)
            fund_part.setdefault("errors", {}).pop("valuation_price_adjusted_fallback", None)
        else:
            fund_part["valuation_latest"] = []
            fund_part.setdefault("errors", {})["valuation"] = "missing valuation row for target date"

        sentiment_errors = {}
        if target_tag not in fund_by_date:
            sentiment_errors["fund_flow"] = "missing historical EM fund-flow row for target date"
            missing_flow.append(target_tag)

        snapshot = {
            "symbol": symbol,
            "as_of": datetime.now().isoformat(timespec="seconds"),
            "data_date": target_iso,
            "fundamentals": fund_part,
            "sentiment": {
                "symbol": symbol,
                "as_of": datetime.now().isoformat(timespec="seconds"),
                "data_date": target_iso,
                "errors": sentiment_errors,
                "fund_flow_recent_20d": fund_recent,
                "fund_flow_source": "em_push2his_history",
                "fund_flow_quality": "strong_em_order_split_history",
                "lhb_source": "skipped_history_backfill",
                "margin_source": "skipped_history_backfill",
                "northbound_source": "skipped_history_backfill",
                "price_recent": price_recent,
                "price_recent_source": "tencent_kline",
            },
            "sector": {
                "symbol": symbol,
                "as_of": datetime.now().isoformat(timespec="seconds"),
                "data_date": target_iso,
                "errors": {},
                "industry_lookup_source": "history_backfill_hardcoded",
                "industry_hardcoded": args.industry,
                "sector_price_source": "pending_enrichment",
            },
        }
        write_json(out_dir / f"{symbol}_snapshot.json", snapshot)
        manifest = out_dir / "_manifest.txt"
        with manifest.open("a", encoding="utf-8") as f:
            f.write(
                f"[backfill] {symbol} {args.name}: history snapshot generated "
                f"from {args.start}-{args.end}; data_date={target_iso}\n"
            )
        written += 1

    calendar_tags = date_range(args.start, args.end)
    non_trading = [tag for tag in calendar_tags if tag not in set(trading_tags)]
    merged = []
    for tag in trading_tags:
        row: dict[str, Any] = {"date": iso_date(tag), "symbol": symbol, "name": args.name}
        row.update({f"price_{k}": v for k, v in price_by_date[tag].items() if k != "date"})
        if tag in fund_by_date:
            row.update(fund_by_date[tag])
        if tag in valuation_by_date:
            row.update({f"valuation_{k}": v for k, v in valuation_by_date[tag].items()})
        merged.append(row)

    history_path = repo_root / "data" / "history" / f"{symbol}_{args.start}_{args.end}.json"
    write_json(
        history_path,
        {
            "symbol": symbol,
            "name": args.name,
            "industry": args.industry,
            "requested_start": iso_date(args.start),
            "requested_end": iso_date(args.end),
            "trading_days": len(trading_tags),
            "non_trading_or_missing_dates": [iso_date(tag) for tag in non_trading],
            "sources": {
                "price": "akshare.stock_zh_a_hist_tx(qfq)",
                "valuation": "akshare.stock_value_em",
                "fund_flow": "EastMoney push2his fflow daykline direct JSONP",
                "financials": "scripts.stock.fetch_fundamentals",
            },
            "missing_target_fund_flow_dates": [iso_date(tag) for tag in missing_flow],
            "records": merged,
        },
    )

    print(
        f"backfilled {symbol} {args.name}: snapshots={written}, "
        f"history_rows={len(merged)}, non_trading_or_missing={len(non_trading)}, "
        f"history={history_path}"
    )
    if missing_flow:
        print(f"missing target fund-flow rows: {', '.join(missing_flow)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
