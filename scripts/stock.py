#!/usr/bin/env python3
"""A-share data fetcher for the china-stock-analysis skill.

Usage:
    python scripts/stock.py snapshot 600519          # three-dim snapshot (default)
    python scripts/stock.py fundamentals 600519      # financial statements & ratios
    python scripts/stock.py sentiment 600519         # money flow, LHB, margin, north
    python scripts/stock.py sector 600519            # sector membership & sector perf
    python scripts/stock.py market                   # market heat / breadth / valuation
    python scripts/stock.py clear-cache              # wipe daily cache

Options:
    --no-cache       skip cache read, still write
    --force          ignore cache entirely
    --json           emit machine-readable JSON to stdout (default: human tables)
    --refresh        equivalent to --force

Data source: akshare (free, open). All calls time-stamped; cached per trading day
under ./.cache/stock/ so repeated runs in the same session don't re-hit the API.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any, Callable

try:
    import akshare as ak
    import pandas as pd
except ImportError as e:
    sys.stderr.write(
        f"ERROR: missing dependency: {e}\n"
        "Install: python -m venv .venv && .venv/bin/pip install akshare pandas\n"
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache" / "stock"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------- utilities ----------

def market_prefix(symbol: str) -> str:
    """Guess SH/SZ/BJ prefix from a 6-digit A-share code."""
    s = symbol.strip()
    if s.startswith(("6", "9")):
        return "sh"
    if s.startswith(("0", "3")):
        return "sz"
    if s.startswith(("4", "8")):
        return "bj"
    raise ValueError(f"Unrecognized A-share symbol: {symbol}")


def today_tag() -> str:
    return date.today().strftime("%Y%m%d")


def cache_path(symbol: str, kind: str) -> Path:
    return CACHE_DIR / f"{symbol}_{kind}_{today_tag()}.json"


def cached(kind: str, symbol: str | None = None):
    """Decorator: cache a function's JSON-serialized output under .cache/stock/<symbol>_<kind>_<date>.json."""
    def wrap(fn: Callable):
        def inner(*args, **kwargs):
            sym = symbol or (args[0] if args else kwargs.get("symbol", "market"))
            path = cache_path(sym, kind)
            use_cache = not kwargs.pop("_force", False)
            if use_cache and path.exists():
                try:
                    return json.loads(path.read_text())
                except Exception:
                    pass
            data = fn(*args, **kwargs)
            try:
                path.write_text(json.dumps(data, ensure_ascii=False, default=str, indent=2))
            except Exception:
                pass
            return data
        return inner
    return wrap


def df_to_records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    return json.loads(df.to_json(orient="records", force_ascii=False, date_format="iso"))


def safe(fn, *args, **kwargs) -> tuple[Any, str | None]:
    """Execute fn; return (result, err). Never raise."""
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


# ---------- dim 1: fundamentals ----------

def fetch_fundamentals(symbol: str, _force: bool = False) -> dict:
    path = cache_path(symbol, "fundamentals")
    if not _force and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass

    out: dict = {"symbol": symbol, "as_of": datetime.now().isoformat(timespec="seconds"), "errors": {}}

    info, err = safe(ak.stock_individual_info_em, symbol=symbol)
    if isinstance(info, pd.DataFrame):
        out["basic_info"] = dict(zip(info["item"], info["value"]))
    if err:
        out["errors"]["basic_info"] = err

    # Primary: Sina's stock_financial_analysis_indicator. Newer akshare builds
    # require start_year; without it the endpoint silently returns an empty frame.
    start_year = str(datetime.now().year - 4)
    ind, err = safe(ak.stock_financial_analysis_indicator, symbol=symbol, start_year=start_year)
    if isinstance(ind, pd.DataFrame) and len(ind) > 0:
        out["financial_indicators_recent"] = df_to_records(ind.tail(8))
    else:
        if err:
            out["errors"]["financial_indicators"] = err
        # Fallback: THS abstract (more reliable coverage for recent reports).
        ths, err2 = safe(ak.stock_financial_abstract_ths, symbol=symbol, indicator="按报告期")
        if isinstance(ths, pd.DataFrame) and len(ths) > 0:
            out["financial_indicators_recent"] = df_to_records(ths.head(8))
            out["financial_indicators_source"] = "ths_abstract"
        elif err2:
            out["errors"]["financial_indicators_ths"] = err2
        else:
            out.setdefault("financial_indicators_recent", [])

    val, err = safe(ak.stock_value_em, symbol=symbol)
    if isinstance(val, pd.DataFrame):
        out["valuation_recent"] = df_to_records(val.tail(20))
        out["valuation_latest"] = df_to_records(val.tail(1))
    if err:
        out["errors"]["valuation"] = err

    divd, err = safe(ak.stock_history_dividend_detail, symbol=symbol, indicator="分红")
    if isinstance(divd, pd.DataFrame):
        out["dividends_recent"] = df_to_records(divd.tail(10))
    if err:
        out["errors"]["dividends"] = err

    path.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    return out


# ---------- dim 2: sentiment ----------

def fetch_sentiment(symbol: str, _force: bool = False) -> dict:
    path = cache_path(symbol, "sentiment")
    if not _force and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass

    out: dict = {"symbol": symbol, "as_of": datetime.now().isoformat(timespec="seconds"), "errors": {}}
    mkt = market_prefix(symbol)

    flow, err = safe(ak.stock_individual_fund_flow, stock=symbol, market=mkt)
    if isinstance(flow, pd.DataFrame):
        out["fund_flow_recent_20d"] = df_to_records(flow.tail(20))
    if err:
        out["errors"]["fund_flow"] = err

    lhb, err = safe(ak.stock_lhb_detail_em,
                    start_date=(datetime.now().replace(month=max(1, datetime.now().month - 3))).strftime("%Y%m%d"),
                    end_date=today_tag())
    if isinstance(lhb, pd.DataFrame) and len(lhb):
        mask = lhb.astype(str).apply(lambda r: symbol in r.values, axis=1)
        out["lhb_recent_3m"] = df_to_records(lhb[mask])
    if err:
        out["errors"]["lhb"] = err

    margin, err = safe(ak.stock_margin_detail_szse if mkt == "sz" else ak.stock_margin_detail_sse,
                       date=today_tag())
    if isinstance(margin, pd.DataFrame):
        row = margin[margin.astype(str).apply(lambda r: symbol in r.values, axis=1)]
        out["margin_trading_today"] = df_to_records(row)
    if err:
        out["errors"]["margin"] = err

    north, err = safe(ak.stock_hsgt_individual_em, symbol=symbol)
    if isinstance(north, pd.DataFrame):
        out["northbound_holdings_recent"] = df_to_records(north.tail(20))
    if err:
        out["errors"]["northbound"] = err

    hist, err = safe(ak.stock_zh_a_hist, symbol=symbol, period="daily",
                     start_date=(date.today().replace(day=1)).strftime("%Y%m%d"),
                     end_date=today_tag(), adjust="qfq")
    if isinstance(hist, pd.DataFrame):
        out["price_recent"] = df_to_records(hist.tail(20))
    if err:
        out["errors"]["price_history"] = err

    path.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    return out


# ---------- dim 3: sector ----------

def fetch_sector(symbol: str, _force: bool = False) -> dict:
    path = cache_path(symbol, "sector")
    if not _force and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass

    out: dict = {"symbol": symbol, "as_of": datetime.now().isoformat(timespec="seconds"), "errors": {}}

    info, err = safe(ak.stock_individual_info_em, symbol=symbol)
    industry = None
    if isinstance(info, pd.DataFrame):
        row = info[info["item"] == "行业"]
        if len(row):
            industry = str(row["value"].iloc[0])
            out["industry_em"] = industry
    if err:
        out["errors"]["industry_lookup"] = err

    if industry:
        hist, err = safe(ak.stock_board_industry_hist_em,
                         symbol=industry,
                         start_date=(date.today().replace(month=max(1, date.today().month - 3))).strftime("%Y%m%d"),
                         end_date=today_tag(),
                         period="daily", adjust="")
        if isinstance(hist, pd.DataFrame):
            out["sector_price_recent"] = df_to_records(hist.tail(20))
        if err:
            out["errors"]["sector_history"] = err

        flow, err = safe(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="行业资金流")
        if isinstance(flow, pd.DataFrame):
            row = flow[flow.astype(str).apply(lambda r: industry in r.values, axis=1)]
            out["sector_fund_flow_today"] = df_to_records(row)
        if err:
            out["errors"]["sector_fund_flow"] = err

    sw, err = safe(ak.sw_index_first_info)
    if isinstance(sw, pd.DataFrame):
        out["sw_index_first_snapshot"] = df_to_records(sw)
    if err:
        out["errors"]["sw_index"] = err

    path.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    return out


# ---------- aggregate: market ----------

def fetch_market(_force: bool = False) -> dict:
    path = cache_path("market", "overview")
    if not _force and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass

    out: dict = {"as_of": datetime.now().isoformat(timespec="seconds"), "errors": {}}

    act, err = safe(ak.stock_market_activity_legu)
    if isinstance(act, pd.DataFrame):
        out["market_activity"] = df_to_records(act)
    if err:
        out["errors"]["market_activity"] = err

    ttm, err = safe(ak.stock_a_ttm_lyr)
    if isinstance(ttm, pd.DataFrame):
        out["all_a_pe_recent"] = df_to_records(ttm.tail(10))
    if err:
        out["errors"]["all_a_pe"] = err

    pb, err = safe(ak.stock_a_all_pb)
    if isinstance(pb, pd.DataFrame):
        out["all_a_pb_recent"] = df_to_records(pb.tail(10))
    if err:
        out["errors"]["all_a_pb"] = err

    zt, err = safe(ak.stock_zt_pool_em, date=today_tag())
    if isinstance(zt, pd.DataFrame):
        out["limit_up_count_today"] = len(zt)
        out["limit_up_sample"] = df_to_records(zt.head(20))
    if err:
        out["errors"]["limit_up"] = err

    dt, err = safe(ak.stock_zt_pool_dtgc_em, date=today_tag())
    if isinstance(dt, pd.DataFrame):
        out["limit_down_count_today"] = len(dt)
    if err:
        out["errors"]["limit_down"] = err

    path.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2))
    return out


# ---------- snapshot (all three dims) ----------

def fetch_snapshot(symbol: str, _force: bool = False) -> dict:
    return {
        "symbol": symbol,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "fundamentals": fetch_fundamentals(symbol, _force=_force),
        "sentiment": fetch_sentiment(symbol, _force=_force),
        "sector": fetch_sector(symbol, _force=_force),
    }


# ---------- rendering ----------

def render_human(payload: dict) -> str:
    lines = []
    sym = payload.get("symbol", "?")
    lines.append(f"=== {sym} · {payload.get('as_of', '')} ===")

    fund = payload.get("fundamentals", payload) if "fundamentals" in payload else payload
    if "basic_info" in fund:
        lines.append("\n[基本信息]")
        for k, v in list(fund["basic_info"].items())[:10]:
            lines.append(f"  {k}: {v}")

    if "valuation_latest" in fund and fund["valuation_latest"]:
        lines.append("\n[估值最新]")
        for k, v in fund["valuation_latest"][0].items():
            lines.append(f"  {k}: {v}")

    if "financial_indicators_recent" in fund and fund["financial_indicators_recent"]:
        lines.append("\n[财务指标最近 4 期]")
        for row in fund["financial_indicators_recent"][-4:]:
            head_key = next(iter(row))
            lines.append(f"  {head_key}: {row[head_key]}")

    sent = payload.get("sentiment", {})
    if sent.get("fund_flow_recent_20d"):
        lines.append("\n[资金流近 5 日]")
        for row in sent["fund_flow_recent_20d"][-5:]:
            first_key = next(iter(row))
            lines.append(f"  {row[first_key]}: {row}")

    sec = payload.get("sector", {})
    if "industry_em" in sec:
        lines.append(f"\n[所属行业] {sec['industry_em']}")
    if sec.get("sector_fund_flow_today"):
        lines.append("[板块今日资金] " + json.dumps(sec["sector_fund_flow_today"], ensure_ascii=False)[:200])

    errs = {}
    for part in ("fundamentals", "sentiment", "sector"):
        if part in payload and payload[part].get("errors"):
            errs[part] = payload[part]["errors"]
    if errs:
        lines.append("\n[数据缺口 / 错误]")
        lines.append(json.dumps(errs, ensure_ascii=False, indent=2))

    return "\n".join(lines)


# ---------- CLI ----------

COMMANDS = {
    "snapshot": fetch_snapshot,
    "fundamentals": fetch_fundamentals,
    "sentiment": fetch_sentiment,
    "sector": fetch_sector,
    "market": fetch_market,
}


def main():
    ap = argparse.ArgumentParser(description="A-share data fetcher (akshare-based)")
    ap.add_argument("command", choices=list(COMMANDS.keys()) + ["clear-cache"])
    ap.add_argument("symbol", nargs="?", help="6-digit A-share code, e.g. 002281")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout")
    ap.add_argument("--force", "--refresh", action="store_true", dest="force",
                    help="bypass cache and re-fetch")
    args = ap.parse_args()

    if args.command == "clear-cache":
        n = 0
        for p in CACHE_DIR.glob("*.json"):
            p.unlink()
            n += 1
        print(f"cleared {n} cache files in {CACHE_DIR}")
        return

    fn = COMMANDS[args.command]
    if args.command == "market":
        data = fn(_force=args.force)
    else:
        if not args.symbol:
            ap.error(f"{args.command} requires a symbol")
        data = fn(args.symbol, _force=args.force)

    if args.json:
        print(json.dumps(data, ensure_ascii=False, default=str, indent=2))
    else:
        print(render_human(data))


if __name__ == "__main__":
    main()
