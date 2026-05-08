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
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Callable

# Force direct connection regardless of system / Windows Internet Settings proxy.
# akshare endpoints (push2*.eastmoney.com, hq.sinajs.cn, etc.) are domestic-China
# hosts that fail when the user has a stale V2Ray/Clash proxy configured but the
# proxy server isn't running, since requests will dutifully try to route through
# the dead proxy and fail with ProxyError. Set BEFORE importing akshare/requests.
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

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


def safe_retry(fn, *args, retries: int = 3, delay: float = 1.5, **kwargs) -> tuple[Any, str | None]:
    """Call safe() with up to `retries` extra attempts on transient network errors."""
    last_err = None
    for i in range(retries + 1):
        result, err = safe(fn, *args, **kwargs)
        if err is None:
            return result, None
        last_err = err
        transient = any(k in err for k in ("SSLError", "ConnectionError", "Timeout",
                                            "RemoteDisconnected", "ChunkedEncodingError",
                                            "ProtocolError", "ReadTimeout",
                                            "JSONDecodeError"))
        if not transient or i == retries:
            break
        time.sleep(delay * (i + 1))
    return None, last_err


def parse_cn_amount(v: Any) -> float | None:
    """Parse '14.15亿' / '5488.68万' / '39.03%' / 123.45 → float. None on failure."""
    if v is None or v is False:
        return None
    if isinstance(v, (int, float)):
        try:
            if v != v:  # NaN
                return None
        except Exception:
            pass
        return float(v)
    s = str(v).strip()
    if not s or s in ("--", "False", "None", "nan", "NaN", "null"):
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1])
        except ValueError:
            return None
    mult = 1.0
    for suf, m in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4), ("千", 1e3)):
        if s.endswith(suf):
            mult = m
            s = s[: -len(suf)]
            break
    try:
        return float(s) * mult
    except ValueError:
        return None


def _normalize_ths_record(rec: dict) -> dict:
    """Clean a THS record: False/'--' → None, Chinese-scaled strings → floats."""
    out: dict = {}
    for k, v in rec.items():
        if k == "报告期":
            out[k] = str(v)[:10] if v not in (None, False) else None
            continue
        if v is False or v is None:
            out[k] = None
            continue
        if isinstance(v, str) and v.strip() in ("", "--", "False", "None", "nan", "NaN"):
            out[k] = None
            continue
        if isinstance(v, (int, float)):
            if isinstance(v, float) and v != v:
                out[k] = None
            else:
                out[k] = float(v)
            continue
        parsed = parse_cn_amount(v)
        out[k] = parsed if parsed is not None else v
    return out


# EM industry name → 申万一级 index code. Used as fallback when EM's industry-board
# history endpoint (stock_board_industry_hist_em) keeps failing with SSL/timeouts.
# 申万一级 codes are stable and the corresponding indices are available via
# index_zh_a_hist. Less granular than EM boards but more reliable.
SECTOR_TO_SW_LEVEL1 = {
    "半导体": "801080",      # 电子
    "消费电子": "801080",    # 电子
    "通信设备": "801770",    # 通信
    "化学制品": "801030",    # 基础化工
    "医疗服务": "801150",    # 医药生物
    "计算机设备": "801750",  # 计算机
    "电子元件": "801080",
    "电子": "801080",
    "通信": "801770",
    "化工": "801030",
    "医药生物": "801150",
    "计算机": "801750",
}

# Industry → 代表 ETF 代码. Used as second-level fallback for sector_price_recent
# when EM industry-board endpoint fails. ETF tracks the broad sector index and
# is stable via akshare fund_etf_hist_em. Only used if SW history endpoints
# don't exist in the running akshare version (true for 1.18.56).
SECTOR_TO_ETF_PROXY = {
    "半导体": "512760",       # 国泰半导体ETF (broad chip exposure)
    "消费电子": "159732",     # 国泰消费电子ETF
    "通信设备": "515880",     # 国泰通信ETF
    "通信": "515880",
    "化学制品": "159870",     # 鹏华化工ETF
    "电子化学品Ⅱ": "159870",  # 申万细分: 电子化学品 (新材料化工)
    "电子化学品": "159870",
    "化工": "159870",
    "医疗服务": "159929",     # 汇添富医药ETF
    "医药生物": "159929",
    "医药商业": "159929",
    "中药": "159929",
    "计算机设备": "159998",   # 华夏计算机ETF
    "计算机应用": "159998",
    "计算机": "159998",
    "电子": "512760",
    "电子元件": "512760",
    "元件": "512760",         # 申万: 元件 (MLCC/被动元件等)
    "航空装备Ⅱ": "512810",    # 易方达国防军工ETF (菲利华 SW 归类)
    "航空装备": "512810",
    "国防军工": "512810",
}


def _coerce_float(v: Any) -> float | None:
    """Best-effort float coercion. Returns None for None/empty/NaN/non-numeric."""
    if v is None or v is False:
        return None
    try:
        if isinstance(v, float) and v != v:  # NaN
            return None
        f = float(v)
        return f
    except (ValueError, TypeError):
        return None


def fetch_total_shares(symbol: str, basic_info: dict | None,
                       valuation_recent: list[dict] | None = None) -> tuple[float | None, str]:
    """Resolve 总股本 (raw share count). Returns (shares, source_tag).

    Priority:
      1) basic_info 总股本 from EM (canonical, matches report-date if recent)
      2) Xueqiu basic info (different backend, immune to EM SSL flakes)
      3) Derive from 总市值 / 最新价 via all-A spot
      4) valuation_recent[-1]['总股本'] from EM stock_value_em (CURRENT shares;
         may differ from report-date if there were buybacks / issuances after,
         so consistency_check should treat this source more leniently).
    """
    if basic_info:
        v = parse_cn_amount(basic_info.get("总股本"))
        if v and v > 1e5:
            return float(v), "em_basic_info"

    # Fallback 1: Xueqiu (different backend, SSL failures independent from EM)
    xq_fn = getattr(ak, "stock_individual_basic_info_xq", None)
    if xq_fn:
        xq_sym = market_prefix(symbol).upper() + symbol
        res, _ = safe_retry(xq_fn, symbol=xq_sym)
        if isinstance(res, pd.DataFrame) and len(res):
            kv = dict(zip(res.iloc[:, 0].astype(str), res.iloc[:, 1]))
            for key in ("总股本", "total_shares", "total_share"):
                v = parse_cn_amount(kv.get(key))
                if v and v > 1e5:
                    return float(v), "xueqiu_basic"

    # Fallback 2: derive from 总市值 / 最新价 via all-A spot (heavy but reliable)
    spot, _ = safe_retry(ak.stock_zh_a_spot_em)
    if isinstance(spot, pd.DataFrame) and "代码" in spot.columns:
        row = spot[spot["代码"].astype(str) == symbol]
        if len(row):
            mcap = parse_cn_amount(row.iloc[0].get("总市值"))
            price = parse_cn_amount(row.iloc[0].get("最新价"))
            if mcap and price and price > 0:
                return float(mcap / price), "em_spot_derived"

    # Fallback 3: valuation_recent (already fetched, no extra network call)
    if valuation_recent:
        try:
            v = _coerce_float(valuation_recent[-1].get("总股本"))
            if v and v > 1e5:
                return float(v), "em_valuation_current"
        except (KeyError, IndexError, TypeError):
            pass

    return None, "unavailable"


def compute_consistency(sina_records: list[dict], ths_records: list[dict],
                        total_shares: float | None, shares_source: str = "unknown") -> dict:
    """Cross-check Sina 扣非EPS × 总股本 against THS 扣非净利润. Flags anomalies.

    Thresholds:
    - |delta| > 8% between Sina-derived and THS absolute → "suspicious"
    - |non-recurring| > 50% of reported net profit → "warn_non_recurring"
      (sign-aware: distinguishes 一次性收益掩盖 vs 一次性损失拖累)
    """
    # Default 8% tolerates Sina EPS 2-digit precision + minor weighted-vs-period-end shares drift.
    # When shares come from "em_valuation_current" (current snapshot, may differ from
    # report-date shares due to buybacks/issuances after period close), loosen to 15%.
    DELTA_THRESHOLD = 15.0 if shares_source == "em_valuation_current" else 8.0
    NONREC_THRESHOLD = 50.0

    result: dict = {"status": "skipped", "notes": []}
    if not sina_records or not ths_records:
        result["reason"] = "missing source"
        return result
    if not total_shares or total_shares < 1e5:
        result["reason"] = "total_shares unavailable"
        return result

    sina_by = {str(r.get("日期", ""))[:10]: r for r in sina_records if r.get("日期")}
    ths_by = {str(r.get("报告期", ""))[:10]: r for r in ths_records if r.get("报告期")}
    common = sorted(set(sina_by) & set(ths_by))
    if not common:
        result["reason"] = "no overlapping periods"
        return result

    latest = common[-1]
    sina_r, ths_r = sina_by[latest], ths_by[latest]
    result.update({
        "status": "ok",
        "latest_period": latest,
        "total_shares": total_shares,
        "shares_source": shares_source,
    })

    def _delta(derived, actual):
        if derived is None or actual is None or actual == 0:
            return None
        return (derived - actual) / actual * 100.0

    # Always surface the raw inputs, independent of cross-check branches
    sina_kf_eps = sina_r.get("扣除非经常性损益后的每股收益(元)")
    sina_eps = sina_r.get("摊薄每股收益(元)")
    ths_kf = ths_r.get("扣非净利润")
    ths_np = ths_r.get("净利润")
    result["sina_kf_eps"] = sina_kf_eps
    result["sina_eps_diluted"] = sina_eps
    result["ths_kf_netprofit"] = ths_kf
    result["ths_netprofit"] = ths_np

    # Cross-check 1: 扣非
    if sina_kf_eps and ths_kf:
        derived = sina_kf_eps * total_shares
        d = _delta(derived, ths_kf)
        result["sina_kf_netprofit_derived"] = derived
        result["kf_delta_pct"] = round(d, 2) if d is not None else None
        if d is not None and abs(d) > DELTA_THRESHOLD:
            result["status"] = "suspicious"
            result["notes"].append(
                f"扣非净利润口径不一致: Sina 推算 {derived/1e8:.2f}亿 vs THS 披露 {ths_kf/1e8:.2f}亿 (差 {d:+.1f}%, >{DELTA_THRESHOLD:.0f}%)"
            )

    # Cross-check 2: 摊薄 / 报告净利润
    if sina_eps and ths_np:
        derived = sina_eps * total_shares
        d = _delta(derived, ths_np)
        result["reported_delta_pct"] = round(d, 2) if d is not None else None
        if d is not None and abs(d) > DELTA_THRESHOLD and result["status"] == "ok":
            result["status"] = "suspicious"
            result["notes"].append(
                f"报告净利润口径不一致: Sina 推算 {derived/1e8:.2f}亿 vs THS 披露 {ths_np/1e8:.2f}亿 (差 {d:+.1f}%, >{DELTA_THRESHOLD:.0f}%)"
            )

    # Non-recurring check — sign-aware
    if ths_np and ths_kf and ths_np > 0:
        nonrec_amt = ths_np - ths_kf  # >0: one-time gains lifted reported profit
        nonrec_pct = nonrec_amt / ths_np * 100
        result["non_recurring_pct"] = round(nonrec_pct, 1)
        result["non_recurring_amount"] = nonrec_amt
        if abs(nonrec_pct) > NONREC_THRESHOLD:
            if nonrec_amt > 0:
                msg = (
                    f"[WARN] 非经常性收益 {nonrec_amt/1e8:.2f}亿 占净利润 {nonrec_pct:.1f}%，"
                    f"报告净利润被一次性收益放大，应以扣非 {ths_kf/1e8:.2f}亿 为主业真实水平"
                )
            else:
                msg = (
                    f"[WARN] 非经常性损失 {-nonrec_amt/1e8:.2f}亿 拖累报告净利润，"
                    f"扣非 {ths_kf/1e8:.2f}亿 反而高于报告 {ths_np/1e8:.2f}亿，主业比账面更好"
                )
            result["notes"].append(msg)
            if result["status"] == "ok":
                result["status"] = "warn_non_recurring"

    return result


# ---------- dim 1: fundamentals ----------

def fetch_fundamentals(symbol: str, _force: bool = False) -> dict:
    path = cache_path(symbol, "fundamentals")
    if not _force and path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass

    out: dict = {"symbol": symbol, "as_of": datetime.now().isoformat(timespec="seconds"), "errors": {}}

    # basic_info: needed for 总股本 → consistency_check. EM's endpoint flakes on SSL occasionally, retry.
    info, err = safe_retry(ak.stock_individual_info_em, symbol=symbol)
    if isinstance(info, pd.DataFrame):
        out["basic_info"] = dict(zip(info["item"], info["value"]))
    if err:
        out["errors"]["basic_info"] = err

    # Sina: ratios (每股 / 盈利能力 / 周转 / 偿债 / 现金流比率 等 80+ 字段).
    # Newer akshare builds require start_year; without it the endpoint silently returns empty.
    start_year = str(datetime.now().year - 4)
    ind, err = safe(ak.stock_financial_analysis_indicator, symbol=symbol, start_year=start_year)
    if isinstance(ind, pd.DataFrame) and len(ind) > 0:
        out["financial_indicators_recent"] = df_to_records(ind.tail(8))
        out["financial_indicators_source"] = "sina_indicator"
    else:
        out["financial_indicators_recent"] = []
        if err:
            out["errors"]["financial_indicators"] = err

    # THS: absolute amounts (营收 / 净利 / 扣非 / 经营现金流 / 总资产 / 毛利率).
    # Fetched independently — NOT as fallback — so we can cross-check against Sina.
    ths, err = safe(ak.stock_financial_abstract_ths, symbol=symbol, indicator="按报告期")
    if isinstance(ths, pd.DataFrame) and len(ths) > 0:
        recent = ths.tail(8) if len(ths) >= 8 else ths
        out["financials_absolute_recent"] = [_normalize_ths_record(r) for r in df_to_records(recent)]
        out["financials_absolute_source"] = "ths_abstract"
    else:
        out["financials_absolute_recent"] = []
        if err:
            out["errors"]["financials_absolute"] = err

    # Fallback: EM 利润表 (stock_profit_sheet_by_report_em) when THS yields nothing.
    # EM uses different field names — DEDUCT_PARENT_NETPROFIT is 扣非归母净利润, etc.
    if not out["financials_absolute_recent"]:
        em_sym = market_prefix(symbol).upper() + symbol  # SH/SZ/BJ + 6-digit
        em_pl, err_em = safe_retry(ak.stock_profit_sheet_by_report_em, symbol=em_sym)
        if isinstance(em_pl, pd.DataFrame) and len(em_pl) > 0:
            # Map EM fields to our format
            recent_em = em_pl.head(8)  # EM is sorted newest-first
            normalized = []
            for _, row in recent_em.iterrows():
                rd = row.get("REPORT_DATE")
                normalized.append({
                    "报告期": str(rd)[:10] if rd is not None else None,
                    "净利润": _coerce_float(row.get("PARENT_NETPROFIT") or row.get("NETPROFIT")),
                    "扣非净利润": _coerce_float(row.get("DEDUCT_PARENT_NETPROFIT")),
                    "营业总收入": _coerce_float(row.get("TOTAL_OPERATE_INCOME") or row.get("OPERATE_INCOME")),
                })
            # Sort oldest-first to match THS convention
            out["financials_absolute_recent"] = list(reversed(normalized))
            out["financials_absolute_source"] = "em_profit_sheet"
            out["errors"].pop("financials_absolute", None)
        elif err_em:
            out["errors"]["financials_absolute_em"] = err_em

    # Valuation BEFORE total_shares so it can be a 4th fallback for shares.
    val, err = safe(ak.stock_value_em, symbol=symbol)
    if isinstance(val, pd.DataFrame):
        out["valuation_recent"] = df_to_records(val.tail(20))
        out["valuation_latest"] = df_to_records(val.tail(1))
    if err:
        out["errors"]["valuation"] = err

    # Cross-source consistency: Sina 扣非EPS × 总股本 vs THS 扣非净利润.
    # 总股本 is resolved through a fallback chain (EM basic_info → Xueqiu → spot-derive
    # → valuation_recent). Note valuation's 总股本 is *current* shares, may differ from
    # report-date shares if there were buybacks/issuances after the report.
    total_shares, shares_source = fetch_total_shares(
        symbol, out.get("basic_info"), out.get("valuation_recent"),
    )
    out["total_shares"] = total_shares
    out["total_shares_source"] = shares_source
    out["consistency_check"] = compute_consistency(
        out.get("financial_indicators_recent", []),
        out.get("financials_absolute_recent", []),
        total_shares,
        shares_source,
    )

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

    flow, err = safe_retry(ak.stock_individual_fund_flow, stock=symbol, market=mkt)
    if isinstance(flow, pd.DataFrame):
        out["fund_flow_recent_20d"] = df_to_records(flow.tail(20))
    if err:
        out["errors"]["fund_flow"] = err

    lhb, err = safe_retry(ak.stock_lhb_detail_em,
                          start_date=(datetime.now().replace(month=max(1, datetime.now().month - 3))).strftime("%Y%m%d"),
                          end_date=today_tag())
    if isinstance(lhb, pd.DataFrame) and len(lhb):
        mask = lhb.astype(str).apply(lambda r: symbol in r.values, axis=1)
        out["lhb_recent_3m"] = df_to_records(lhb[mask])
    if err:
        out["errors"]["lhb"] = err

    # Margin: SSE/SZSE often haven't published today's data when called early evening,
    # producing either SSL errors or akshare's "Length mismatch" (empty frame, columns
    # assigned to nothing). Try today first, fall back to yesterday on failure.
    margin_fn = ak.stock_margin_detail_szse if mkt == "sz" else ak.stock_margin_detail_sse
    margin = None
    err = None
    for try_date in (today_tag(), (date.today() - timedelta(days=1)).strftime("%Y%m%d")):
        margin, err = safe_retry(margin_fn, date=try_date)
        if isinstance(margin, pd.DataFrame) and len(margin) > 0:
            out["margin_trading_date"] = try_date
            break
    if isinstance(margin, pd.DataFrame) and len(margin) > 0:
        row = margin[margin.astype(str).apply(lambda r: symbol in r.values, axis=1)]
        out["margin_trading_today"] = df_to_records(row)
    elif err:
        out["errors"]["margin"] = err

    north, err = safe_retry(ak.stock_hsgt_individual_em, symbol=symbol)
    if isinstance(north, pd.DataFrame):
        out["northbound_holdings_recent"] = df_to_records(north.tail(20))
    if err:
        out["errors"]["northbound"] = err

    hist, err = safe_retry(ak.stock_zh_a_hist, symbol=symbol, period="daily",
                           start_date=(date.today() - timedelta(days=60)).strftime("%Y%m%d"),
                           end_date=today_tag(), adjust="qfq")
    if isinstance(hist, pd.DataFrame) and len(hist) > 0:
        out["price_recent"] = df_to_records(hist.tail(20))
        out["price_recent_source"] = "em_kline"
    else:
        if err:
            out["errors"]["price_history"] = err
        # Fallback: Tencent kline (different backend) when EM consistently times out.
        tx_fn = getattr(ak, "stock_zh_a_hist_tx", None)
        if tx_fn:
            tx_sym = market_prefix(symbol) + symbol
            hist2, err2 = safe_retry(tx_fn, symbol=tx_sym,
                                     start_date=(date.today() - timedelta(days=60)).strftime("%Y%m%d"),
                                     end_date=today_tag(), adjust="qfq")
            if isinstance(hist2, pd.DataFrame) and len(hist2) > 0:
                out["price_recent"] = df_to_records(hist2.tail(20))
                out["price_recent_source"] = "tencent_kline"
                out["errors"].pop("price_history", None)
            elif err2:
                out["errors"]["price_history_tx"] = err2

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

    info, err = safe_retry(ak.stock_individual_info_em, symbol=symbol)
    industry = None
    em_err = err
    xq_err = None
    if isinstance(info, pd.DataFrame):
        row = info[info["item"] == "行业"]
        if len(row):
            industry = str(row["value"].iloc[0])
            out["industry_em"] = industry

    # Fallback 1: Xueqiu basic info (different backend)
    if not industry:
        xq_fn = getattr(ak, "stock_individual_basic_info_xq", None)
        if xq_fn:
            xq_sym = market_prefix(symbol).upper() + symbol
            xq_res, xq_err = safe_retry(xq_fn, symbol=xq_sym)
            if isinstance(xq_res, pd.DataFrame) and len(xq_res):
                kv = dict(zip(xq_res.iloc[:, 0].astype(str), xq_res.iloc[:, 1]))
                for key in ("所属行业", "行业", "industry"):
                    v = kv.get(key)
                    if v and str(v).strip() not in ("", "--", "None"):
                        industry = str(v)
                        out["industry_xq"] = industry
                        break

    # Fallback 2: hardcoded watchlist mapping. Used when both EM and Xueqiu fail
    # (EM often returns empty body → JSONDecodeError; Xueqiu often missing 'data' key).
    # Names use EM industry vocabulary so stock_board_industry_hist_em can consume them.
    WATCHLIST_INDUSTRY_FALLBACK = {
        "002281": "通信设备", "000988": "通信设备",
        "688008": "半导体",   "603986": "半导体",   "688728": "半导体",
        "688332": "半导体",   "688380": "半导体",   "688123": "半导体",
        "688046": "医疗服务",
        "688550": "化学制品",
        "688208": "计算机设备",
        "002475": "消费电子",
        "300458": "半导体",
        "601869": "通信设备",  # 长飞光纤 - 光纤光缆
        "600522": "通信设备",  # 中天科技 - 光纤+海缆
        "600487": "通信设备",  # 亨通光电 - 光纤+海缆
        "300395": "半导体",    # 菲利华 - 石英材料(半导体光刻核心耗材)
        "300408": "电子元件",  # 三环集团 - MLCC陶瓷电子元件
    }
    if not industry and symbol in WATCHLIST_INDUSTRY_FALLBACK:
        industry = WATCHLIST_INDUSTRY_FALLBACK[symbol]
        out["industry_hardcoded"] = industry

    if not industry:
        errs_combined = []
        if em_err: errs_combined.append(f"em: {em_err}")
        if xq_err: errs_combined.append(f"xq: {xq_err}")
        if errs_combined:
            out["errors"]["industry_lookup"] = "; ".join(errs_combined)

    if industry:
        hist, err = safe_retry(ak.stock_board_industry_hist_em,
                               symbol=industry,
                               start_date=(date.today().replace(month=max(1, date.today().month - 3))).strftime("%Y%m%d"),
                               end_date=today_tag(),
                               period="daily", adjust="")
        if isinstance(hist, pd.DataFrame) and len(hist) > 0:
            out["sector_price_recent"] = df_to_records(hist.tail(20))
            out["sector_price_source"] = "em_industry_board"
        else:
            if err:
                out["errors"]["sector_history"] = err
            # Fallback 1: EM ETF endpoint. fund_etf_hist_em is on push2/data.eastmoney
            # which sometimes also gets RemoteDisconnected blocking, so we further
            # fall back to Tencent kline using the ETF code as a stock symbol.
            etf_code = SECTOR_TO_ETF_PROXY.get(industry)
            if etf_code:
                etf_start = (date.today() - timedelta(days=120)).strftime("%Y%m%d")
                etf_end = today_tag()

                # Try EM first
                etf_hist, etf_err = None, None
                etf_fn = getattr(ak, "fund_etf_hist_em", None)
                if etf_fn:
                    etf_hist, etf_err = safe_retry(
                        etf_fn, symbol=etf_code, period="daily",
                        start_date=etf_start, end_date=etf_end, adjust="qfq",
                    )
                if isinstance(etf_hist, pd.DataFrame) and len(etf_hist) > 0:
                    out["sector_price_recent"] = df_to_records(etf_hist.tail(20))
                    out["sector_price_source"] = f"etf_em_{etf_code}"
                    out["errors"].pop("sector_history", None)
                else:
                    if etf_err:
                        out["errors"]["sector_history_etf"] = etf_err
                    elif isinstance(etf_hist, pd.DataFrame):
                        out["errors"]["sector_history_etf"] = f"empty result for ETF {etf_code}"

                    # Fallback 2: Tencent kline using ETF code as stock symbol.
                    # ETFs trade like stocks; sh51xxxx/sh588xxx and sz15xxxx/sz16xxxx
                    # all have continuous K-line via stock_zh_a_hist_tx (Tencent host
                    # is consistently reachable when EM is blocked).
                    if not (isinstance(etf_hist, pd.DataFrame) and len(etf_hist) > 0):
                        prefix = "sh" if etf_code.startswith(("5", "6")) else "sz"
                        tx_etf_sym = prefix + etf_code
                        tx_fn = getattr(ak, "stock_zh_a_hist_tx", None)
                        if tx_fn:
                            tx_hist, tx_err = safe_retry(
                                tx_fn, symbol=tx_etf_sym,
                                start_date=etf_start, end_date=etf_end, adjust="qfq",
                            )
                            if isinstance(tx_hist, pd.DataFrame) and len(tx_hist) > 0:
                                out["sector_price_recent"] = df_to_records(tx_hist.tail(20))
                                out["sector_price_source"] = f"etf_tencent_{etf_code}"
                                out["errors"].pop("sector_history", None)
                                out["errors"].pop("sector_history_etf", None)
                            elif tx_err:
                                out["errors"]["sector_history_etf_tx"] = tx_err

        flow, err = safe_retry(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="行业资金流")
        if isinstance(flow, pd.DataFrame):
            row = flow[flow.astype(str).apply(lambda r: industry in r.values, axis=1)]
            out["sector_fund_flow_today"] = df_to_records(row)
        if err:
            out["errors"]["sector_fund_flow"] = err

    sw, err = safe_retry(ak.sw_index_first_info)
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

    if fund.get("consistency_check"):
        cc = fund["consistency_check"]
        icon = {"ok": "[OK]", "suspicious": "[WARN]", "warn_non_recurring": "[WARN]",
                "skipped": "[--]"}.get(cc.get("status"), "[?]")
        lines.append(f"\n[数据一致性] {icon} {cc.get('status')}")
        if cc.get("latest_period"):
            lines.append(f"  对比期: {cc['latest_period']}")
        if cc.get("kf_delta_pct") is not None:
            lines.append(
                f"  扣非净利润 Sina推算 {cc.get('sina_kf_netprofit_derived', 0)/1e8:.2f}亿 "
                f"vs THS披露 {cc.get('ths_kf_netprofit', 0)/1e8:.2f}亿 "
                f"(差 {cc['kf_delta_pct']:+.1f}%)"
            )
        if cc.get("non_recurring_pct") is not None:
            lines.append(f"  非经常损益占净利润: {cc['non_recurring_pct']:.1f}%")
        for note in cc.get("notes", []):
            lines.append(f"  · {note}")

    if fund.get("financials_absolute_recent"):
        lines.append("\n[主干金额近 4 期 · 同花顺]")
        def _fmt(v):
            if v is None: return "—"
            if isinstance(v, (int, float)):
                if abs(v) >= 1e8: return f"{v/1e8:.2f}亿"
                if abs(v) >= 1e4: return f"{v/1e4:.2f}万"
                return f"{v:.2f}"
            return str(v)
        for row in fund["financials_absolute_recent"][-4:]:
            period = row.get("报告期", "?")
            rev = row.get("营业总收入")
            netp = row.get("净利润")
            kf = row.get("扣非净利润")
            lines.append(f"  {period}  营收 {_fmt(rev):>9} | 净利 {_fmt(netp):>9} | 扣非 {_fmt(kf):>9}")

    if fund.get("financial_indicators_recent"):
        lines.append("\n[关键比率近 4 期 · 新浪]")
        for row in fund["financial_indicators_recent"][-4:]:
            period = str(row.get("日期", ""))[:10]
            roe = row.get("加权净资产收益率(%)")
            gm = row.get("销售毛利率(%)")
            nm = row.get("销售净利率(%)")
            dr = row.get("资产负债率(%)")
            kf_eps = row.get("扣除非经常性损益后的每股收益(元)")
            def _v(x, suf=""):
                return f"{x:.2f}{suf}" if isinstance(x, (int, float)) else "—"
            lines.append(
                f"  {period}  ROE {_v(roe, '%'):>7} | 毛利 {_v(gm, '%'):>7} | "
                f"净利率 {_v(nm, '%'):>7} | 负债 {_v(dr, '%'):>7} | 扣非EPS {_v(kf_eps):>6}"
            )

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
