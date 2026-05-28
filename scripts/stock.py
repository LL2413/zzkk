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
import signal
import socket
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

# Global socket timeout. akshare endpoints have no per-call timeout, so a
# half-dead host (accepts the connection then never sends data) hangs the
# whole fetch indefinitely — observed as a 57-minute stall on one symbol.
# setdefaulttimeout makes any socket op with no progress for STOCK_NET_TIMEOUT
# seconds raise TimeoutError, which safe_retry treats as a transient failure
# (retry, then record as an error and move on). Override via env if needed.
STOCK_NET_TIMEOUT = float(os.environ.get("STOCK_NET_TIMEOUT", "12"))
socket.setdefaulttimeout(STOCK_NET_TIMEOUT)


def env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


# Some AkShare wrappers paginate/retry internally, so the socket timeout above
# can still leave one logical call waiting for many minutes. Put a wall-clock
# cap around each source call; the caller records it as a warning and continues
# through the existing fallback chain.
STOCK_CALL_TIMEOUT = float(os.environ.get("STOCK_CALL_TIMEOUT", "30"))
STOCK_FAST_DAILY = env_flag("STOCK_FAST_DAILY")
STOCK_FETCH_SECTOR_FUND_FLOW = env_flag("STOCK_FETCH_SECTOR_FUND_FLOW")
STOCK_REUSE_LOW_FREQ_FINANCIALS = env_flag("STOCK_REUSE_LOW_FREQ_FINANCIALS")
STOCK_FINANCIALS_MODE = os.environ.get("STOCK_FINANCIALS_MODE", "").strip().lower()
if not STOCK_FINANCIALS_MODE:
    STOCK_FINANCIALS_MODE = "reuse" if STOCK_REUSE_LOW_FREQ_FINANCIALS else "refresh"
if STOCK_FINANCIALS_MODE not in {"refresh", "reuse", "auto"}:
    STOCK_FINANCIALS_MODE = "refresh"
STOCK_FINANCIALS_SOURCE_DATE = os.environ.get("STOCK_FINANCIALS_SOURCE_DATE", "").strip()
STOCK_SKIP_EM_FUND_FLOW_RANK = env_flag("STOCK_SKIP_EM_FUND_FLOW_RANK", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_LHB = env_flag("STOCK_SKIP_LHB", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_MARGIN = env_flag("STOCK_SKIP_MARGIN", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_NORTHBOUND = env_flag("STOCK_SKIP_NORTHBOUND", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_SECTOR_PRICE = env_flag("STOCK_SKIP_SECTOR_PRICE", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_SW_INDEX = env_flag("STOCK_SKIP_SW_INDEX", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_BASIC_INFO = env_flag("STOCK_SKIP_BASIC_INFO", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_VALUATION = env_flag("STOCK_SKIP_VALUATION", "1" if STOCK_FAST_DAILY else "0")
STOCK_SKIP_EM_FUND_FLOW_HISTORY = env_flag(
    "STOCK_SKIP_EM_FUND_FLOW_HISTORY", "1" if STOCK_FAST_DAILY else "0"
)
STOCK_SKIP_INDUSTRY_LOOKUP = env_flag("STOCK_SKIP_INDUSTRY_LOOKUP", "1" if STOCK_FAST_DAILY else "0")


class StockCallTimeout(TimeoutError):
    pass


def _raise_stock_call_timeout(signum, frame):
    raise StockCallTimeout(f"source call exceeded {STOCK_CALL_TIMEOUT:.0f}s")

try:
    import akshare as ak
    import pandas as pd
    import requests
except ImportError as e:
    sys.stderr.write(
        f"ERROR: missing dependency: {e}\n"
        "Install: python -m venv .venv && .venv/bin/pip install akshare pandas\n"
    )
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / ".cache" / "stock"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

BASIC_INFO_DROP_KEYS = {"上市时间"}
LOW_FREQ_FINANCIAL_KEYS = (
    "financial_indicators_recent",
    "financial_indicators_source",
    "financials_absolute_recent",
    "financials_absolute_source",
    "dividends_recent",
)


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


def run_date() -> date:
    tag = os.environ.get("STOCK_DATE_TAG", "").strip()
    if tag:
        try:
            return datetime.strptime(tag, "%Y%m%d").date()
        except ValueError:
            pass
    return date.today()


def today_tag() -> str:
    return run_date().strftime("%Y%m%d")


def today_iso() -> str:
    return run_date().strftime("%Y-%m-%d")


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


def prune_basic_info(info: dict | None) -> dict | None:
    if not isinstance(info, dict):
        return info
    return {k: v for k, v in info.items() if k not in BASIC_INFO_DROP_KEYS}


def load_low_frequency_financials(symbol: str) -> tuple[dict | None, str | None]:
    """Load financial statement/dividend fields from a prior daily snapshot."""
    data_root = ROOT / "data"
    if not data_root.exists():
        return None, None

    today = today_tag()
    tags: list[str]
    if STOCK_FINANCIALS_SOURCE_DATE:
        tags = [STOCK_FINANCIALS_SOURCE_DATE]
    else:
        tags = sorted(
            (
                p.name for p in data_root.iterdir()
                if p.is_dir() and p.name.isdigit() and len(p.name) == 8 and p.name < today
            ),
            reverse=True,
        )

    for tag in tags:
        path = data_root / tag / f"{symbol}_snapshot.json"
        if not path.exists():
            continue
        try:
            snap = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        fund = snap.get("fundamentals") if isinstance(snap, dict) else None
        if not isinstance(fund, dict):
            continue
        if fund.get("financial_indicators_recent") or fund.get("financials_absolute_recent"):
            reused = {key: fund.get(key) for key in LOW_FREQ_FINANCIAL_KEYS if key in fund}
            return reused, tag
    return None, None


def safe(fn, *args, **kwargs) -> tuple[Any, str | None]:
    """Execute fn; return (result, err). Never raise."""
    use_alarm = STOCK_CALL_TIMEOUT > 0 and hasattr(signal, "SIGALRM")
    old_handler = None
    old_timer = None
    try:
        if use_alarm:
            old_handler = signal.getsignal(signal.SIGALRM)
            old_timer = signal.setitimer(signal.ITIMER_REAL, STOCK_CALL_TIMEOUT)
            signal.signal(signal.SIGALRM, _raise_stock_call_timeout)
        return fn(*args, **kwargs), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)
            if old_timer and old_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, old_timer[0], old_timer[1])


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
                                            "JSONDecodeError", "timed out"))
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


def normalize_stock_code(v: Any) -> str:
    """Return a zero-padded 6-digit stock code from strings/numbers."""
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0"):
        s = s[:-2]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits[-6:].zfill(6)


EM_FUND_FLOW_RANK_FIELDS = "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f124"
EM_FUND_FLOW_RANK_FS = (
    "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
    "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
)
EM_FUND_FLOW_RANK_URLS = (
    "https://push2.eastmoney.com/api/qt/clist/get",
    "http://push2.eastmoney.com/api/qt/clist/get",
    "https://29.push2.eastmoney.com/api/qt/clist/get",
    "https://90.push2.eastmoney.com/api/qt/clist/get",
)
EM_FUND_FLOW_ULIST_URLS = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://29.push2.eastmoney.com/api/qt/ulist.np/get",
    "https://90.push2.eastmoney.com/api/qt/ulist.np/get",
)
_EM_FUND_FLOW_RANK_RECORDS: list[dict[str, Any]] | None = None
_EM_FUND_FLOW_RANK_FAILURE: str | None = None
_EM_FUND_FLOW_ULIST_RECORDS: dict[str, dict[str, Any]] | None = None


def _em_secid(symbol: str) -> str:
    """EastMoney secid: 1.x for SH, 0.x for SZ/BJ-style watchlist symbols."""
    code = normalize_stock_code(symbol)
    market = "1" if code.startswith(("6", "9")) else "0"
    return f"{market}.{code}"


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _proxy_url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if "://" not in value:
        value = "http://" + value
    return value


def _windows_system_proxy() -> dict[str, str] | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return None
            server = winreg.QueryValueEx(key, "ProxyServer")[0]
    except Exception:
        return None

    if not isinstance(server, str) or not server.strip():
        return None
    if "=" not in server:
        proxy = _proxy_url(server)
        return {"http": proxy, "https": proxy} if proxy else None

    parsed: dict[str, str] = {}
    for part in server.split(";"):
        if "=" not in part:
            continue
        scheme, value = part.split("=", 1)
        proxy = _proxy_url(value)
        if proxy and scheme.strip().lower() in {"http", "https"}:
            parsed[scheme.strip().lower()] = proxy
    if "http" in parsed and "https" not in parsed:
        parsed["https"] = parsed["http"]
    if "https" in parsed and "http" not in parsed:
        parsed["http"] = parsed["https"]
    return parsed or None


def _em_proxy_candidates() -> list[tuple[str, dict[str, str] | None]]:
    candidates: list[tuple[str, dict[str, str] | None]] = [("direct", None)]
    explicit = _proxy_url(os.environ.get("STOCK_HTTP_PROXY") or os.environ.get("STOCK_PROXY"))
    if explicit:
        candidates.append(("STOCK_PROXY", {"http": explicit, "https": explicit}))
    win_proxy = _windows_system_proxy()
    if win_proxy:
        candidates.append(("windows_proxy", win_proxy))
    return candidates


def _em_num(v: Any) -> float | None:
    """Parse EastMoney numeric fields; '-' and - become None."""
    if v is None or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _em_timestamp_date(v: Any) -> str | None:
    try:
        ts = int(float(v))
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _em_rank_row_to_flow(row: dict[str, Any], as_of: str | None = None) -> dict | None:
    """Convert EastMoney clist rank fields into the project's fund-flow schema."""
    symbol = normalize_stock_code(row.get("f12") or row.get("代码") or row.get("股票代码"))
    main_net = _em_num(row.get("f62"))
    if not symbol or main_net is None:
        return None
    source_date = as_of or _em_timestamp_date(row.get("f124")) or today_iso()
    return {
        "日期": source_date,
        "股票代码": symbol,
        "股票简称": row.get("f14") or row.get("名称") or row.get("股票简称"),
        "最新价": _em_num(row.get("f2")),
        "涨跌幅": _em_num(row.get("f3")),
        "主力净流入-净额": main_net,
        "主力净流入-净占比": _em_num(row.get("f184")),
        "超大单净流入-净额": _em_num(row.get("f66")),
        "超大单净流入-净占比": _em_num(row.get("f69")),
        "大单净流入-净额": _em_num(row.get("f72")),
        "大单净流入-净占比": _em_num(row.get("f75")),
        "中单净流入-净额": _em_num(row.get("f78")),
        "中单净流入-净占比": _em_num(row.get("f81")),
        "小单净流入-净额": _em_num(row.get("f84")),
        "小单净流入-净占比": _em_num(row.get("f87")),
        "fund_flow_quality": "strong_em_order_split",
        "fund_flow_note": "EastMoney超大单+大单主力口径；主力=f66超大单+f72大单",
        "em_update_time": _em_timestamp_date(row.get("f124")),
    }


def _ak_rank_row_to_flow(row: dict[str, Any], as_of: str | None = None) -> dict | None:
    symbol = normalize_stock_code(row.get("代码") or row.get("股票代码"))
    if not symbol:
        return None
    rec: dict[str, Any] = {
        "日期": as_of or today_iso(),
        "股票代码": symbol,
        "股票简称": row.get("名称") or row.get("股票简称"),
        "最新价": parse_cn_amount(row.get("最新价")),
    }
    for key, value in row.items():
        if isinstance(key, str) and key.startswith("今日"):
            rec[key[len("今日"):]] = parse_cn_amount(value)
    if rec.get("主力净流入-净额") is None:
        return None
    rec["fund_flow_quality"] = "strong_em_order_split"
    rec["fund_flow_note"] = "EastMoney超大单+大单主力口径；来自akshare rank"
    return rec


def fetch_em_ulist_fund_flow_today(symbols: list[str], as_of: str | None = None) -> tuple[dict[str, dict], str | None]:
    """Fetch EM order-split fund flow for specific symbols via small batched quotes.

    The full-market clist rank endpoint is prone to gateway resets on some
    networks. ulist.np lets us request only the watchlist secids while still
    returning f62/f66/f72/f78/f84, so it is the preferred strong口径 path.
    """
    global _EM_FUND_FLOW_ULIST_RECORDS

    wanted = []
    for symbol in symbols:
        code = normalize_stock_code(symbol)
        if code and code not in wanted:
            wanted.append(code)
    if not wanted:
        return {}, "no symbols requested"

    cache = cache_path("all", "em_fund_flow_ulist")
    raw_by_symbol: dict[str, dict[str, Any]] = _EM_FUND_FLOW_ULIST_RECORDS or {}
    errors: list[str] = []

    if not raw_by_symbol and cache.exists():
        try:
            loaded = json.loads(cache.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, list):
                raw_by_symbol = {
                    normalize_stock_code(row.get("f12")): row
                    for row in loaded
                    if normalize_stock_code(row.get("f12"))
                }
            elif isinstance(loaded, dict):
                raw_by_symbol = {
                    normalize_stock_code(k): v
                    for k, v in loaded.items()
                    if normalize_stock_code(k) and isinstance(v, dict)
                }
            _EM_FUND_FLOW_ULIST_RECORDS = raw_by_symbol
        except Exception:
            raw_by_symbol = {}

    missing = [symbol for symbol in wanted if symbol not in raw_by_symbol]
    if missing:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/zjlx/detail.html",
            "Accept": "application/json,text/plain,*/*",
        }
        session = requests.Session()
        session.trust_env = False
        proxy_candidates = _em_proxy_candidates()
        for chunk in _chunks(missing, 40):
            params = {
                "secids": ",".join(_em_secid(symbol) for symbol in chunk),
                "ut": "b2884a393a59ad64002292a3e90d46a5",
                "fltt": "2",
                "invt": "2",
                "fields": EM_FUND_FLOW_RANK_FIELDS,
                "_": int(time.time() * 1000),
            }
            chunk_ok = False
            for url in EM_FUND_FLOW_ULIST_URLS:
                for proxy_label, proxies in proxy_candidates:
                    try:
                        resp = session.get(
                            url,
                            params=params,
                            headers=headers,
                            timeout=STOCK_NET_TIMEOUT,
                            proxies=proxies,
                        )
                        if resp.status_code != 200:
                            errors.append(f"{url} [{proxy_label}]: HTTP {resp.status_code}")
                            continue
                        payload = resp.json()
                        diff = (payload.get("data") or {}).get("diff") or []
                        if not isinstance(diff, list) or not diff:
                            errors.append(f"{url} [{proxy_label}]: empty diff")
                            continue
                        for row in diff:
                            code = normalize_stock_code(row.get("f12"))
                            if code:
                                raw_by_symbol[code] = row
                        chunk_ok = True
                        break
                    except Exception as exc:
                        errors.append(f"{url} [{proxy_label}]: {type(exc).__name__}: {exc}")
                if chunk_ok:
                    break
            if not chunk_ok:
                errors.append(f"ulist chunk failed: {','.join(chunk)}")

        if raw_by_symbol:
            _EM_FUND_FLOW_ULIST_RECORDS = raw_by_symbol
            try:
                cache.write_text(
                    json.dumps(list(raw_by_symbol.values()), ensure_ascii=False, default=str, indent=2),
                    encoding="utf-8",
                )
            except Exception:
                pass

    result: dict[str, dict] = {}
    for symbol in wanted:
        row = raw_by_symbol.get(symbol)
        if not row:
            continue
        rec = _em_rank_row_to_flow(row, as_of=as_of)
        if rec:
            rec["fund_flow_note"] = "EastMoney超大单+大单主力口径；来自ulist.np小批量接口"
            result[symbol] = rec

    missing_after = [symbol for symbol in wanted if symbol not in result]
    if missing_after:
        errors.append(f"missing EM ulist flow: {','.join(missing_after)}")
    return result, "; ".join(errors) if errors else None


def fetch_em_rank_fund_flow_today(symbol: str, as_of: str | None = None) -> tuple[dict | None, str | None]:
    """Fetch today's EM order-split fund flow from the all-market rank endpoint.

    This is the strong same-day口径: EastMoney exposes 主力净流入 plus its
    超大单/大单/中单/小单 split. It is one-day only, unlike
    stock_individual_fund_flow's history endpoint, but it is still the correct
    strong口径 for same-day watchlist scans.
    """
    global _EM_FUND_FLOW_RANK_RECORDS, _EM_FUND_FLOW_RANK_FAILURE

    symbol = normalize_stock_code(symbol)
    cache = cache_path("all", "em_fund_flow_rank")
    records: list[dict[str, Any]] | None = _EM_FUND_FLOW_RANK_RECORDS
    errors: list[str] = []

    ulist, ulist_err = fetch_em_ulist_fund_flow_today([symbol], as_of=as_of)
    if symbol in ulist:
        return ulist[symbol], None
    if ulist_err:
        errors.append(f"EM ulist: {ulist_err}")

    if records is None and _EM_FUND_FLOW_RANK_FAILURE:
        return None, _EM_FUND_FLOW_RANK_FAILURE

    if cache.exists():
        try:
            loaded = json.loads(cache.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, list) and loaded:
                records = loaded
                _EM_FUND_FLOW_RANK_RECORDS = records
        except Exception:
            records = None

    if records is None:
        params = {
            "fid": "f62",
            "po": "1",
            "pz": "10000",
            "pn": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "fs": EM_FUND_FLOW_RANK_FS,
            "fields": EM_FUND_FLOW_RANK_FIELDS,
            "_": int(time.time() * 1000),
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/zjlx/detail.html",
            "Accept": "application/json,text/plain,*/*",
        }
        session = requests.Session()
        session.trust_env = False
        for url in EM_FUND_FLOW_RANK_URLS:
            try:
                resp = session.get(url, params=params, headers=headers, timeout=STOCK_NET_TIMEOUT)
                if resp.status_code != 200:
                    errors.append(f"{url}: HTTP {resp.status_code}")
                    continue
                payload = resp.json()
                diff = (payload.get("data") or {}).get("diff") or []
                if isinstance(diff, list) and diff:
                    records = diff
                    _EM_FUND_FLOW_RANK_RECORDS = records
                    cache.write_text(
                        json.dumps(records, ensure_ascii=False, default=str, indent=2),
                        encoding="utf-8",
                    )
                    break
                errors.append(f"{url}: empty diff")
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")

    if records:
        row = next((r for r in records if normalize_stock_code(r.get("f12")) == symbol), None)
        if row:
            rec = _em_rank_row_to_flow(row, as_of=as_of)
            if rec:
                return rec, None
        errors.append(f"{symbol} not found in EM rank table")

    # Last resort for the strong口径: AkShare's wrapper around the same EM rank
    # endpoint. It is slower and can fail during full-market pagination, so use
    # it only after the direct one-page call above.
    rank_fn = getattr(ak, "stock_individual_fund_flow_rank", None)
    if rank_fn is not None:
        rank, rank_err = safe_retry(rank_fn, indicator="今日", retries=0, delay=0.8)
        if isinstance(rank, pd.DataFrame) and len(rank) > 0:
            code_col = next((c for c in rank.columns if c in ("代码", "股票代码")), None)
            if code_col is not None:
                codes = rank[code_col].astype(str).str.zfill(6)
                row_df = rank[codes == symbol]
                if len(row_df) > 0:
                    rec = _ak_rank_row_to_flow(row_df.iloc[0].to_dict(), as_of=as_of)
                    if rec:
                        return rec, None
            errors.append(f"{symbol} not found in AkShare rank frame")
        elif rank_err:
            errors.append(f"akshare rank: {rank_err}")

    err_msg = "; ".join(errors) if errors else "EM rank unavailable"
    if records is None:
        _EM_FUND_FLOW_RANK_FAILURE = err_msg
    return None, err_msg


def fetch_ths_fund_flow_today(symbol: str, as_of: str | None = None) -> tuple[dict | None, str | None]:
    """Fallback today's per-stock fund flow from THS.

    THS exposes total inflow/outflow/net amount, not EastMoney's
    super+large-order "main" classification. We map THS net amount into the
    existing keys so downstream enrichers can still compute same-day scans,
    while `fund_flow_source` records the weaker data口径.
    """
    cache = cache_path("all", "ths_fund_flow_individual")
    records: list[dict] | None = None

    if cache.exists():
        try:
            loaded = json.loads(cache.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, list) and loaded:
                records = loaded
        except Exception:
            records = None

    if records is None:
        ths_fn = getattr(ak, "stock_fund_flow_individual", None)
        if ths_fn is None:
            return None, "ak.stock_fund_flow_individual unavailable"
        table, err = safe_retry(ths_fn, symbol="即时", retries=1, delay=2)
        if not isinstance(table, pd.DataFrame) or len(table) == 0:
            return None, err or "THS individual fund-flow returned empty"
        records = df_to_records(table)
        try:
            cache.write_text(json.dumps(records, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        except Exception:
            pass

    row = next((r for r in records if normalize_stock_code(r.get("股票代码")) == symbol), None)
    if not row:
        return None, f"{symbol} not found in THS individual fund-flow table"

    net = parse_cn_amount(row.get("净额"))
    amount = parse_cn_amount(row.get("成交额"))
    if net is None:
        return None, f"THS row for {symbol} missing 净额"

    net_pct = None
    if amount and amount != 0:
        net_pct = round(net / amount * 100, 4)

    rec = {
        "日期": as_of or today_iso(),
        "股票代码": normalize_stock_code(row.get("股票代码")),
        "股票简称": row.get("股票简称"),
        "最新价": parse_cn_amount(row.get("最新价")),
        "涨跌幅": parse_cn_amount(row.get("涨跌幅")),
        "换手率": parse_cn_amount(row.get("换手率")),
        "主力净流入-净额": net,
        "主力净流入-净占比": net_pct,
        "流入资金": parse_cn_amount(row.get("流入资金")),
        "流出资金": parse_cn_amount(row.get("流出资金")),
        "成交额": amount,
        "fallback_note": "THS净额fallback；不是EastMoney超大单+大单主力口径",
    }
    return rec, None


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
    "电源设备": "801730",    # 电力设备
    "电力设备": "801730",
    "软件开发": "801750",    # 计算机
    "软件": "801750",
    "光学光电子": "801080",  # 电子下属
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
    "自动化设备": "515880",   # 华工科技 SW 归 自动化设备, 但实际是光模块+激光, 用通信 ETF 对标
    "激光设备": "515880",
    "通信运营": "515880",
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

    out: dict = {
        "symbol": symbol,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "data_date": today_iso(),
        "errors": {},
    }

    # basic_info: needed for 总股本 → consistency_check. EM endpoint flakes on
    # ConnectionError/SSL frequently; fall back chain:
    #   em (full info) → xq (full info) → spot_em (single row from market table)
    if STOCK_SKIP_BASIC_INFO:
        out["basic_info_source"] = "skipped_fast_daily"
    else:
        em_info, em_err = safe_retry(ak.stock_individual_info_em, symbol=symbol)
        if isinstance(em_info, pd.DataFrame) and len(em_info) > 0:
            out["basic_info"] = prune_basic_info(dict(zip(em_info["item"], em_info["value"])))
            out["basic_info_source"] = "em"
        else:
            xq_fn = getattr(ak, "stock_individual_basic_info_xq", None)
            xq_err = None
            if xq_fn:
                xq_sym = market_prefix(symbol).upper() + symbol
                xq_info, xq_err = safe_retry(xq_fn, symbol=xq_sym)
                if isinstance(xq_info, pd.DataFrame) and len(xq_info) > 0:
                    kv = dict(zip(xq_info.iloc[:, 0].astype(str), xq_info.iloc[:, 1]))
                    out["basic_info"] = prune_basic_info(kv)
                    out["basic_info_source"] = "xq"
            # 3rd source: spot_em returns a market-wide snapshot; pluck the row for
            # this symbol. Only gives name/price/mcap/PE etc, but better than empty.
            spot_err = None
            if "basic_info" not in out:
                spot, spot_err = safe_retry(ak.stock_zh_a_spot_em)
                if isinstance(spot, pd.DataFrame) and len(spot) > 0 and "代码" in spot.columns:
                    row = spot[spot["代码"] == symbol]
                    if len(row) > 0:
                        out["basic_info"] = prune_basic_info(row.iloc[0].to_dict())
                        out["basic_info_source"] = "spot_em"
            if "basic_info" not in out:
                errs = []
                if em_err: errs.append(f"em: {em_err}")
                if xq_err: errs.append(f"xq: {xq_err}")
                if spot_err: errs.append(f"spot_em: {spot_err}")
                out["errors"]["basic_info"] = "; ".join(errs) if errs else "no source returned data"

    if STOCK_FINANCIALS_MODE in {"reuse", "auto"}:
        reused, source_tag = load_low_frequency_financials(symbol)
        if reused:
            out.update(reused)
            out["financials_frequency"] = "low_frequency_reused"
            out["financials_source_date"] = source_tag
            if STOCK_SKIP_VALUATION:
                out["valuation_source"] = "skipped_fast_daily"
            else:
                val, err = safe_retry(ak.stock_value_em, symbol=symbol)
                if isinstance(val, pd.DataFrame):
                    out["valuation_recent"] = df_to_records(val.tail(20))
                    out["valuation_latest"] = df_to_records(val.tail(1))
                if err:
                    out["errors"]["valuation"] = err

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

            path.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2))
            return out
        out["errors"]["low_frequency_financials"] = "no prior snapshot with financial fields found"
        if STOCK_FINANCIALS_MODE == "reuse":
            out["financials_frequency"] = "low_frequency_missing"
            out["financial_indicators_recent"] = []
            out["financials_absolute_recent"] = []
            if STOCK_SKIP_VALUATION:
                out["valuation_source"] = "skipped_fast_daily"
            else:
                val, err = safe_retry(ak.stock_value_em, symbol=symbol)
                if isinstance(val, pd.DataFrame):
                    out["valuation_recent"] = df_to_records(val.tail(20))
                    out["valuation_latest"] = df_to_records(val.tail(1))
                if err:
                    out["errors"]["valuation"] = err
            total_shares, shares_source = fetch_total_shares(
                symbol, out.get("basic_info"), out.get("valuation_recent"),
            )
            out["total_shares"] = total_shares
            out["total_shares_source"] = shares_source
            out["consistency_check"] = compute_consistency([], [], total_shares, shares_source)
            path.write_text(json.dumps(out, ensure_ascii=False, default=str, indent=2))
            return out

    # Sina: ratios (每股 / 盈利能力 / 周转 / 偿债 / 现金流比率 等 80+ 字段).
    # Newer akshare builds require start_year; without it the endpoint silently returns empty.
    start_year = str(run_date().year - 4)
    ind, err = safe_retry(ak.stock_financial_analysis_indicator, symbol=symbol, start_year=start_year)
    if isinstance(ind, pd.DataFrame) and len(ind) > 0:
        out["financial_indicators_recent"] = df_to_records(ind.tail(8))
        out["financial_indicators_source"] = "sina_indicator"
    else:
        out["financial_indicators_recent"] = []
        if err:
            out["errors"]["financial_indicators"] = err

    # THS: absolute amounts (营收 / 净利 / 扣非 / 经营现金流 / 总资产 / 毛利率).
    # Fetched independently — NOT as fallback — so we can cross-check against Sina.
    ths, err = safe_retry(ak.stock_financial_abstract_ths, symbol=symbol, indicator="按报告期")
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
    if STOCK_SKIP_VALUATION:
        out["valuation_source"] = "skipped_fast_daily"
    else:
        val, err = safe_retry(ak.stock_value_em, symbol=symbol)
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

    divd, err = safe_retry(ak.stock_history_dividend_detail, symbol=symbol, indicator="分红")
    if isinstance(divd, pd.DataFrame):
        out["dividends_recent"] = df_to_records(divd.tail(10))
    if err:
        out["errors"]["dividends"] = err
    out["financials_frequency"] = "low_frequency_refreshed"
    out["financials_source_date"] = today_tag()

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

    out: dict = {
        "symbol": symbol,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "data_date": today_iso(),
        "errors": {},
    }
    mkt = market_prefix(symbol)

    if STOCK_SKIP_EM_FUND_FLOW_HISTORY:
        flow, err = None, "skipped_fast_daily"
    else:
        flow, err = safe_retry(ak.stock_individual_fund_flow, stock=symbol, market=mkt, retries=0, delay=0.8)
    if isinstance(flow, pd.DataFrame) and len(flow) > 0:
        out["fund_flow_recent_20d"] = df_to_records(flow.tail(20))
        out["fund_flow_source"] = "em_individual"
        out["fund_flow_quality"] = "strong_em_order_split_history"
    else:
        if err and err != "skipped_fast_daily":
            out["errors"]["fund_flow"] = err

        # Fallback 1: EM all-market rank endpoint. This is still the strong
        # EastMoney order-split口径 (主力=超大单+大单), but today's row only.
        if "fund_flow_recent_20d" not in out and not STOCK_SKIP_EM_FUND_FLOW_RANK:
            em_rec, em_err = fetch_em_rank_fund_flow_today(symbol)
            if em_rec:
                out["fund_flow_recent_20d"] = [em_rec]
                out["fund_flow_source"] = "em_rank_today_order_split"
                out["fund_flow_quality"] = "strong_em_order_split_today_only"
                out["errors"].pop("fund_flow", None)
                out["errors"].pop("fund_flow_fallback", None)
                out["errors"].pop("fund_flow_fallback_ths", None)
            elif em_err:
                out["errors"]["fund_flow_fallback_em_rank"] = em_err
        elif "fund_flow_recent_20d" not in out and STOCK_SKIP_EM_FUND_FLOW_RANK:
            out["errors"]["fund_flow_fallback_em_rank"] = (
                "skipped by STOCK_SKIP_EM_FUND_FLOW_RANK after repeated EM endpoint failures"
            )

        # Fallback 2: THS per-stock net flow. This is weaker than EM's
        # super+large-order "main" classification, but it uses a different
        # host and keeps same-day scans/enrichers alive when EM push2/push2his
        # is throttled. Reports must mark this as weak口径.
        if "fund_flow_recent_20d" not in out:
            ths_rec, ths_err = fetch_ths_fund_flow_today(symbol)
            if ths_rec:
                out["fund_flow_recent_20d"] = [ths_rec]
                out["fund_flow_source"] = "ths_individual_net_today_only"
                out["fund_flow_quality"] = "weak_ths_net_today_only"
                out["fund_flow_fallback_note"] = (
                    "THS净额fallback；不是EastMoney超大单+大单主力口径"
                )
                out["errors"].pop("fund_flow", None)
                out["errors"].pop("fund_flow_fallback", None)
            elif ths_err:
                out["errors"]["fund_flow_fallback_ths"] = ths_err

    if STOCK_SKIP_LHB:
        out["lhb_source"] = "skipped_fast_daily"
    else:
        lhb, err = safe_retry(ak.stock_lhb_detail_em,
                              start_date=(run_date() - timedelta(days=90)).strftime("%Y%m%d"),
                              end_date=today_tag())
        if isinstance(lhb, pd.DataFrame) and len(lhb):
            mask = lhb.astype(str).apply(lambda r: symbol in r.values, axis=1)
            out["lhb_recent_3m"] = df_to_records(lhb[mask])
        if err:
            out["errors"]["lhb"] = err

    # Margin: SSE/SZSE often haven't published today's data when called early evening,
    # producing either SSL errors or akshare's "Length mismatch" (empty frame, columns
    # assigned to nothing). Walk back up to 5 business days until we hit published data.
    if STOCK_SKIP_MARGIN:
        out["margin_source"] = "skipped_fast_daily"
    else:
        margin_fn = ak.stock_margin_detail_szse if mkt == "sz" else ak.stock_margin_detail_sse
        margin = None
        err = None
        attempted: list[str] = []
        d = run_date()
        tries = 0
        while tries < 5:
            if d.weekday() < 5:  # Mon-Fri only; skip Sat/Sun
                try_date = d.strftime("%Y%m%d")
                margin, err = safe_retry(margin_fn, date=try_date, retries=0, delay=0.8)
                attempted.append(try_date)
                if isinstance(margin, pd.DataFrame) and len(margin) > 0:
                    out["margin_trading_date"] = try_date
                    break
                tries += 1
            d -= timedelta(days=1)
        if isinstance(margin, pd.DataFrame) and len(margin) > 0:
            row = margin[margin.astype(str).apply(lambda r: symbol in r.values, axis=1)]
            out["margin_trading_today"] = df_to_records(row)
        elif err:
            # akshare raises "Length mismatch: Expected axis has 0 elements" when the
            # exchange returned an empty file (data not yet published). Surface a clean
            # message instead of the pandas internals.
            msg = str(err)
            if "Length mismatch" in msg or "axis has 0 elements" in msg or "BadZipFile" in msg:
                err = f"margin data not yet published for {','.join(attempted)} (exchange returned empty)"
            out["errors"]["margin"] = err

    if STOCK_SKIP_NORTHBOUND:
        out["northbound_source"] = "skipped_fast_daily"
    else:
        north, err = safe_retry(ak.stock_hsgt_individual_em, symbol=symbol)
        if isinstance(north, pd.DataFrame):
            out["northbound_holdings_recent"] = df_to_records(north.tail(20))
        if err:
            out["errors"]["northbound"] = err

    hist, err = safe_retry(ak.stock_zh_a_hist, symbol=symbol, period="daily",
                           start_date=(run_date() - timedelta(days=60)).strftime("%Y%m%d"),
                           end_date=today_tag(), adjust="qfq")
    if isinstance(hist, pd.DataFrame) and len(hist) > 0:
        # EM kline returns Chinese column names; Tencent kline (the fallback
        # below) returns English. Normalize EM to the English schema so every
        # snapshot's price_recent has the same keys regardless of source.
        hist = hist.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交额": "amount",
            "成交量": "volume", "涨跌幅": "pct_chg",
        })
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
                                     start_date=(run_date() - timedelta(days=60)).strftime("%Y%m%d"),
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

    out: dict = {
        "symbol": symbol,
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "data_date": today_iso(),
        "errors": {},
    }

    industry = None
    em_err = None
    xq_err = None
    if not STOCK_SKIP_INDUSTRY_LOOKUP:
        info, err = safe_retry(ak.stock_individual_info_em, symbol=symbol)
        em_err = err
        if isinstance(info, pd.DataFrame):
            row = info[info["item"] == "行业"]
            if len(row):
                industry = str(row["value"].iloc[0])
                out["industry_em"] = industry
    else:
        out["industry_lookup_source"] = "skipped_fast_daily"

    # Fallback 1: Xueqiu basic info (different backend)
    if not industry and not STOCK_SKIP_INDUSTRY_LOOKUP:
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
        "603256": "电子元件",  # 宏和科技 - 电子布/玻纤(PCB+半导体封装上游)
        "603773": "光学光电子", # 沃格光电 - 光电玻璃精加工(显示面板上游)
        "000021": "半导体",    # 深科技 - 存储芯片封测
        "688627": "半导体",    # 精智达 - 半导体/显示测试设备
        "688206": "半导体",    # 概伦电子 - EDA
        "688521": "半导体",    # 芯原股份 - 芯片设计服务/IP
        "688047": "半导体",    # 龙芯中科 - 国产 CPU
        "600845": "软件开发",  # 宝信软件 - 工业软件/IDC
        "300499": "电源设备",  # 高澜股份 - 电力电子温控/液冷
        "002837": "电源设备",  # 英维克 - 精密温控/数据中心液冷
        "002156": "半导体",    # 通富微电 - 半导体封测
        "600584": "半导体",    # 长电科技 - 半导体封测
        "688981": "半导体",    # 中芯国际 - 晶圆代工
        "688347": "半导体",    # 华虹公司 - 晶圆代工
        "601138": "通信设备",  # 工业富联 - 服务器/AI 算力硬件
        "600118": "航天航空",  # 中国卫星 - 航天器制造
        "600879": "航天航空",  # 航天电子 - 航天电子设备
        "001270": "半导体",    # 铖昌科技 - 相控阵 T/R 芯片
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

    if industry and STOCK_SKIP_SECTOR_PRICE:
        out["sector_price_source"] = "skipped_fast_daily"

    if industry and not STOCK_SKIP_SECTOR_PRICE:
        sector_start = (run_date() - timedelta(days=90)).strftime("%Y%m%d")
        hist, err = safe_retry(ak.stock_board_industry_hist_em,
                               symbol=industry,
                               start_date=sector_start,
                               end_date=today_tag(),
                               period="daily", adjust="")
        if isinstance(hist, pd.DataFrame) and len(hist) > 0:
            out["sector_price_recent"] = df_to_records(hist.tail(20))
            out["sector_price_source"] = "em_industry_board"
        else:
            if err:
                out["errors"]["sector_history"] = err

            # Fallback 1 (preferred): 申万一级 index via index_zh_a_hist.
            # SW level-1 is the canonical Chinese sector taxonomy; the endpoint
            # is hosted separately from EM's push2 so it stays up when EM is
            # blocked. Less granular than EM industry board (5 codes cover 33
            # stocks) but the data is clean and trustworthy for cross-stock
            # comparison. Put BEFORE ETF: ETF tracks a sector but adds tracking
            # error and pricing noise; SW is the source of truth.
            sw_code = SECTOR_TO_SW_LEVEL1.get(industry)
            if sw_code:
                sw_hist, sw_err = safe_retry(
                    ak.index_zh_a_hist, symbol=sw_code, period="daily",
                    start_date=(run_date() - timedelta(days=120)).strftime("%Y%m%d"),
                    end_date=today_tag(),
                )
                if isinstance(sw_hist, pd.DataFrame) and len(sw_hist) > 0:
                    out["sector_price_recent"] = df_to_records(sw_hist.tail(20))
                    out["sector_price_source"] = f"sw_index_{sw_code}"
                    out["errors"].pop("sector_history", None)
                elif sw_err:
                    out["errors"]["sector_history_sw"] = sw_err

            # Fallback 2: EM ETF endpoint. fund_etf_hist_em is on push2/data.eastmoney
            # which sometimes also gets RemoteDisconnected blocking, so we further
            # fall back to Tencent kline using the ETF code as a stock symbol.
            if "sector_price_recent" in out:
                etf_code = None  # SW already resolved; skip ETF
            else:
                etf_code = SECTOR_TO_ETF_PROXY.get(industry)
            if etf_code:
                etf_start = (run_date() - timedelta(days=120)).strftime("%Y%m%d")
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

        # If any fallback ultimately populated sector_price_recent, the
        # intermediate-source errors are not real gaps — drop them so the
        # validator doesn't flag a resolved fetch as a warning.
        if out.get("sector_price_recent"):
            for _k in ("sector_history", "sector_history_sw",
                       "sector_history_etf", "sector_history_etf_tx"):
                out["errors"].pop(_k, None)

        # Sector fund flow: use the post-fetch watchlist aggregation by default.
        # The per-stock EM sector endpoint is repeatedly slow/noisy on this
        # network and duplicates data later written to sector_flow_aggregated.json.
        if STOCK_FETCH_SECTOR_FUND_FLOW:
            flow_err = None
            for fn_name, kwargs, search_industry in (
                ("stock_sector_fund_flow_rank",    {"indicator": "今日", "sector_type": "行业资金流"}, True),
                ("stock_sector_fund_flow_summary", {"symbol": industry, "indicator": "今日"},          False),
            ):
                fn = getattr(ak, fn_name, None)
                if not fn:
                    continue
                flow, err = safe_retry(fn, **kwargs)
                if isinstance(flow, pd.DataFrame) and len(flow) > 0:
                    if search_industry:
                        row = flow[flow.astype(str).apply(lambda r: industry in r.values, axis=1)]
                    else:
                        row = flow  # summary already filtered to this industry
                    if len(row) > 0:
                        out["sector_fund_flow_today"] = df_to_records(row)
                        out["sector_fund_flow_source"] = fn_name
                        flow_err = None
                        break
                    flow_err = f"{fn_name}: industry '{industry}' not in frame"
                elif err:
                    flow_err = f"{fn_name}: {err}"
            if flow_err and "sector_fund_flow_today" not in out:
                out["errors"]["sector_fund_flow"] = flow_err
        else:
            out["sector_fund_flow_source"] = "skipped_use_sector_flow_aggregated"

    if STOCK_SKIP_SW_INDEX:
        out["sw_index_source"] = "skipped_fast_daily"
    else:
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

    out: dict = {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "data_date": today_iso(),
        "errors": {},
    }

    act, err = safe_retry(ak.stock_market_activity_legu)
    if isinstance(act, pd.DataFrame):
        out["market_activity"] = df_to_records(act)
    else:
        if err:
            out["errors"]["market_activity"] = err
        # Fallback: derive breadth from full A-share spot. legu's scrape
        # target goes down intermittently (AttributeError: NoneType.text),
        # but stock_zh_a_spot_em is a reliable JSON endpoint already used
        # elsewhere in this script. It yields up/down/flat counts — the core
        # 赚钱效应 signal — minus legu's composite activity index.
        spot, spot_err = safe_retry(ak.stock_zh_a_spot_em)
        if (isinstance(spot, pd.DataFrame) and len(spot) > 0
                and "涨跌幅" in spot.columns):
            chg = pd.to_numeric(spot["涨跌幅"], errors="coerce").dropna()
            out["market_breadth_fallback"] = {
                "up": int((chg > 0).sum()),
                "down": int((chg < 0).sum()),
                "flat": int((chg == 0).sum()),
                "median_pct": round(float(chg.median()), 2),
                "source": "stock_zh_a_spot_em (legu unavailable)",
            }
            out["errors"].pop("market_activity", None)
        elif spot_err:
            out["errors"]["market_activity_fallback"] = spot_err

    ttm, err = safe_retry(ak.stock_a_ttm_lyr)
    if isinstance(ttm, pd.DataFrame):
        out["all_a_pe_recent"] = df_to_records(ttm.tail(10))
    if err:
        out["errors"]["all_a_pe"] = err

    pb, err = safe_retry(ak.stock_a_all_pb)
    if isinstance(pb, pd.DataFrame):
        out["all_a_pb_recent"] = df_to_records(pb.tail(10))
    if err:
        out["errors"]["all_a_pb"] = err

    zt, err = safe_retry(ak.stock_zt_pool_em, date=today_tag())
    if isinstance(zt, pd.DataFrame):
        out["limit_up_count_today"] = len(zt)
        out["limit_up_sample"] = df_to_records(zt.head(20))
    if err:
        out["errors"]["limit_up"] = err

    dt, err = safe_retry(ak.stock_zt_pool_dtgc_em, date=today_tag())
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
        "data_date": today_iso(),
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
