#!/usr/bin/env python3
"""Generate a Markdown watchlist report from local china-stock snapshots.

The report is intentionally deterministic: it reads data/YYYYMMDD snapshots,
compares the previous available trading day, and writes a concise Chinese
watchlist analysis without needing a live LLM session.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


DATE_KEY = "日期"
NAME_KEY = "股票简称"
CODE_KEY = "股票代码"
CHG_KEY = "涨跌幅"
MAIN_NET_KEY = "主力净流入-净额"
MAIN_PCT_KEY = "主力净流入-净占比"
STRONG_FUND_FLOW_SOURCES = {
    "em_individual",
    "em_rank_today_order_split",
    "em_rank_today_only",
}


@dataclass
class StockRow:
    symbol: str
    name: str
    industry: str
    chg_pct: float | None
    month_pct: float | None
    main_yi: float | None
    main_pct: float | None
    source: str
    streak_dir: str
    streak_days: int
    streak_yi: float | None
    divergence: str
    divergence_score: float | None
    signal_total: float | None
    signal_tier: str
    valuation: str
    alpha: float | None
    sector_chg: float | None
    new_symbol: bool
    score_delta: float | None = None
    main_delta_yi: float | None = None


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, help="Target data/YYYYMMDD directory")
    ap.add_argument("--date", help="Target date tag YYYYMMDD")
    ap.add_argument("--previous-date", help="Previous date tag for comparison")
    ap.add_argument("--repo-root", type=Path, default=repo_root_from_script())
    ap.add_argument("--output", type=Path, help="Markdown output path")
    ap.add_argument("--lookback", type=int, default=40, help="How many data dirs to use for cross-day streaks")
    ap.add_argument("--require-strong-fund-flow", action="store_true",
                    help="fail if any snapshot lacks EastMoney order-split fund-flow口径")
    return ap.parse_args()


def data_dirs(repo_root: Path) -> list[Path]:
    data_root = repo_root / "data"
    if not data_root.exists():
        return []
    return sorted(
        p for p in data_root.iterdir()
        if p.is_dir() and p.name.isdigit() and len(p.name) == 8
    )


def iso_date(tag: str) -> str:
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"


def zh_date(tag: str) -> str:
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"


def find_target_dir(args: argparse.Namespace) -> Path:
    root = args.repo_root.resolve()
    if args.data_dir:
        return args.data_dir.resolve()
    if args.date:
        return root / "data" / args.date
    dirs = data_dirs(root)
    if not dirs:
        raise SystemExit("ERROR: no data/YYYYMMDD directories found")
    return dirs[-1]


def previous_dir(repo_root: Path, target_tag: str, explicit: str | None = None) -> Path | None:
    if explicit:
        p = repo_root / "data" / explicit
        return p if p.is_dir() else None
    prev = [p for p in data_dirs(repo_root) if p.name < target_tag]
    return prev[-1] if prev else None


def number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {"--", "None", "nan", "NaN", "null"}:
        return None
    if s.endswith("%"):
        s = s[:-1]
    mult = 1.0
    for suffix, factor in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4), ("千", 1e3)):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            mult = factor
            break
    try:
        return float(s.replace(",", "")) * mult
    except ValueError:
        return None


def pct(v: float | None) -> str:
    return "N/A" if v is None else f"{v:+.2f}%"


def yi(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.2f}亿"


def compact_yi(v: float | None) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.1f}亿"


def signed(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "N/A"
    return f"{v:+.{digits}f}"


def safe_name(data: dict[str, Any], flow_row: dict[str, Any] | None = None) -> str:
    basic = data.get("fundamentals", {}).get("basic_info", {})
    return (
        str(basic.get(NAME_KEY) or "")
        or str((flow_row or {}).get(NAME_KEY) or "")
        or str(data.get("symbol", ""))
    )


def safe_industry(data: dict[str, Any]) -> str:
    basic = data.get("fundamentals", {}).get("basic_info", {})
    sector = data.get("sector", {})
    return str(
        basic.get("行业")
        or sector.get("industry_hardcoded")
        or sector.get("industry_em")
        or sector.get("industry_xq")
        or "未分类"
    )


def row_date(row: dict[str, Any]) -> str:
    return str(row.get(DATE_KEY) or row.get("date") or "")[:10]


def find_dated_row(rows: list[dict[str, Any]], target_iso: str) -> dict[str, Any] | None:
    exact = [r for r in rows if row_date(r) == target_iso]
    if exact:
        return exact[-1]
    older = [r for r in rows if row_date(r) and row_date(r) <= target_iso]
    if older:
        return sorted(older, key=row_date)[-1]
    if len(rows) == 1:
        return rows[0]
    return None


def flow_row(data: dict[str, Any], target_iso: str) -> dict[str, Any] | None:
    rows = data.get("sentiment", {}).get("fund_flow_recent_20d") or []
    if not isinstance(rows, list):
        return None
    return find_dated_row(rows, target_iso)


def price_row(data: dict[str, Any], target_iso: str) -> dict[str, Any] | None:
    rows = data.get("sentiment", {}).get("price_recent") or []
    if not isinstance(rows, list):
        return None
    return find_dated_row(rows, target_iso)


def month_return(data: dict[str, Any], target_iso: str) -> float | None:
    rows = data.get("sentiment", {}).get("price_recent") or []
    rows = [r for r in rows if isinstance(r, dict) and str(r.get("date", ""))[:10] <= target_iso]
    rows = sorted(rows, key=lambda r: str(r.get("date", "")))
    if len(rows) < 2:
        return None
    first = number(rows[0].get("close"))
    last = number(rows[-1].get("close"))
    if not first or not last:
        return None
    return (last / first - 1) * 100


def current_chg(data: dict[str, Any], target_iso: str, flow: dict[str, Any] | None) -> float | None:
    flow_chg = number((flow or {}).get(CHG_KEY))
    if flow_chg is not None:
        return flow_chg
    rows = data.get("sentiment", {}).get("price_recent") or []
    rows = sorted(
        [r for r in rows if isinstance(r, dict) and str(r.get("date", ""))[:10] <= target_iso],
        key=lambda r: str(r.get("date", "")),
    )
    if len(rows) < 2:
        return None
    prev = number(rows[-2].get("close"))
    last = number(rows[-1].get("close"))
    if not prev or not last:
        return None
    return (last / prev - 1) * 100


def valuation_label(data: dict[str, Any]) -> str:
    v = data.get("fundamentals", {}).get("_valuation_summary") or {}
    metric = str(v.get("primary_metric") or "")
    value = number(v.get("primary_value"))
    if value is None:
        return "N/A"
    if metric == "PE_TTM":
        return f"PE{value:.0f}"
    if metric == "PB":
        return f"PB{value:.1f}"
    if metric == "PS":
        return f"PS{value:.1f}"
    return f"{metric}{value:.1f}"


def load_snapshots(data_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(data_dir.glob("*_snapshot.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        symbol = str(data.get("symbol") or path.name[:6]).zfill(6)
        out[symbol] = data
    return out


def build_flow_history(repo_root: Path, target_tag: str, lookback: int) -> dict[str, list[dict[str, Any]]]:
    dirs = [p for p in data_dirs(repo_root) if p.name <= target_tag][-lookback:]
    history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in dirs:
        tag = d.name
        target_iso = iso_date(tag)
        for symbol, data in load_snapshots(d).items():
            flow = flow_row(data, target_iso)
            if not flow:
                continue
            main_net = number(flow.get(MAIN_NET_KEY))
            main_pct = number(flow.get(MAIN_PCT_KEY))
            chg = current_chg(data, target_iso, flow)
            if main_net is None and main_pct is None:
                continue
            history[symbol].append({
                "tag": tag,
                "date": target_iso,
                "main_yi": main_net / 1e8 if main_net is not None else None,
                "main_pct": main_pct,
                "chg_pct": chg,
            })
    for rows in history.values():
        rows.sort(key=lambda r: r["tag"])
    return history


def sign_from_record(rec: dict[str, Any]) -> int:
    v = rec.get("main_pct")
    if v is None:
        v = rec.get("main_yi")
    if v is None:
        return 0
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def compute_cross_day_streak(series: list[dict[str, Any]], target_tag: str) -> tuple[str, int, float | None]:
    current = [r for r in series if r["tag"] <= target_tag]
    if not current or current[-1]["tag"] != target_tag:
        return "flat", 0, None
    sign = sign_from_record(current[-1])
    if sign == 0:
        return "flat", 1, 0.0
    days = 0
    total = 0.0
    has_total = False
    for rec in reversed(current):
        if sign_from_record(rec) != sign:
            break
        days += 1
        if rec.get("main_yi") is not None:
            total += float(rec["main_yi"])
            has_total = True
    return ("positive" if sign > 0 else "negative"), days, (total if has_total else None)


def stock_row(
    symbol: str,
    data: dict[str, Any],
    target_iso: str,
    target_tag: str,
    history: dict[str, list[dict[str, Any]]],
    prev_rows: dict[str, "StockRow"],
) -> StockRow:
    flow = flow_row(data, target_iso)
    main_net = number((flow or {}).get(MAIN_NET_KEY))
    main_yi = main_net / 1e8 if main_net is not None else None
    main_pct = number((flow or {}).get(MAIN_PCT_KEY))
    sig = data.get("_signal_score") or {}
    div = data.get("sentiment", {}).get("_divergence") or {}
    alpha = data.get("sector", {}).get("_alpha_vs_sector") or {}
    streak_dir, streak_days, streak_yi = compute_cross_day_streak(history.get(symbol, []), target_tag)
    row = StockRow(
        symbol=symbol,
        name=safe_name(data, flow),
        industry=safe_industry(data),
        chg_pct=current_chg(data, target_iso, flow),
        month_pct=month_return(data, target_iso),
        main_yi=main_yi,
        main_pct=main_pct,
        source=str(data.get("sentiment", {}).get("fund_flow_source") or "unknown"),
        streak_dir=streak_dir,
        streak_days=streak_days,
        streak_yi=streak_yi,
        divergence=str(div.get("interpretation") or ""),
        divergence_score=number(div.get("score")),
        signal_total=number(sig.get("total")),
        signal_tier=str(sig.get("tier") or ""),
        valuation=valuation_label(data),
        alpha=number(alpha.get("alpha")),
        sector_chg=number(alpha.get("sector_chg")),
        new_symbol=symbol not in prev_rows,
    )
    prev = prev_rows.get(symbol)
    if prev:
        if row.signal_total is not None and prev.signal_total is not None:
            row.score_delta = row.signal_total - prev.signal_total
        if row.main_yi is not None and prev.main_yi is not None:
            row.main_delta_yi = row.main_yi - prev.main_yi
    return row


def sorted_by_flow(rows: list[StockRow]) -> list[StockRow]:
    return sorted(rows, key=lambda r: (r.main_yi is None, -(r.main_yi or -10**9)))


def score_bucket(row: StockRow) -> str:
    if row.signal_total is None:
        return "未知"
    if row.signal_total >= 2:
        return "偏多"
    if row.signal_total >= 0:
        return "中性"
    return "警示"


def tier_counts(rows: list[StockRow]) -> Counter[str]:
    c: Counter[str] = Counter()
    for row in rows:
        c[score_bucket(row)] += 1
    return c


def source_counts(rows: list[StockRow]) -> Counter[str]:
    c: Counter[str] = Counter()
    for row in rows:
        c[row.source] += 1
    return c


def error_counts(snapshots: dict[str, dict[str, Any]]) -> Counter[str]:
    out: Counter[str] = Counter()
    for data in snapshots.values():
        for part in ("fundamentals", "sentiment", "sector"):
            errors = data.get(part, {}).get("errors") or {}
            if isinstance(errors, dict):
                for key in errors:
                    out[f"{part}.{key}"] += 1
    return out


def sector_rows(repo_root: Path, target_tag: str, prev_tag: str | None) -> list[dict[str, Any]]:
    target_file = repo_root / "data" / target_tag / "sector_flow_aggregated.json"
    if not target_file.exists():
        return []
    target = load_json(target_file).get("sectors") or {}
    prev: dict[str, Any] = {}
    if prev_tag:
        prev_file = repo_root / "data" / prev_tag / "sector_flow_aggregated.json"
        if prev_file.exists():
            prev = load_json(prev_file).get("sectors") or {}
    rows: list[dict[str, Any]] = []
    for name, info in target.items():
        curr = number(info.get("main_net_total_yi"))
        before = number((prev.get(name) or {}).get("main_net_total_yi"))
        delta = curr - before if curr is not None and before is not None else None
        signal = "延续"
        if curr is not None and before is not None:
            if before >= 0 and curr < 0:
                signal = "转弱"
            elif before < 0 and curr >= 0:
                signal = "转强"
            elif curr < 0 and delta is not None and delta < 0:
                signal = "放大流出"
            elif curr > 0 and delta is not None and delta > 0:
                signal = "加速流入"
        rows.append({
            "sector": name,
            "current": curr,
            "previous": before,
            "delta": delta,
            "members": int(info.get("member_count") or len(info.get("members") or [])),
            "signal": signal,
        })
    rows.sort(key=lambda r: abs(r["current"] or 0), reverse=True)
    return rows


def market_summary(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "market.json"
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except Exception:
        return {}
    pe_rows = data.get("all_a_pe_recent") or []
    pb_rows = data.get("all_a_pb_recent") or []
    return {
        "pe": pe_rows[-1] if pe_rows else {},
        "pb": pb_rows[-1] if pb_rows else {},
        "limit_up": data.get("limit_up_count_today"),
        "limit_down": data.get("limit_down_count_today"),
        "errors": data.get("errors") or {},
    }


def top_names(rows: list[StockRow], limit: int = 3) -> str:
    if not rows:
        return "无"
    return "、".join(f"{r.name}{compact_yi(r.main_yi)}" for r in rows[:limit])


def short_names(rows: list[StockRow], limit: int = 5) -> str:
    if not rows:
        return "无"
    return "、".join(f"{r.name}{r.symbol}" for r in rows[:limit])


def table(headers: list[str], body: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def streak_text(row: StockRow) -> str:
    if row.streak_days <= 0:
        return "N/A"
    prefix = "+" if row.streak_dir == "positive" else ("-" if row.streak_dir == "negative" else "0")
    return f"{prefix}{row.streak_days}d"


def row_reason(row: StockRow) -> str:
    bits = []
    if row.main_yi is not None:
        bits.append(f"主力{compact_yi(row.main_yi)}")
    if row.chg_pct is not None:
        bits.append(f"涨跌{pct(row.chg_pct)}")
    if row.main_pct is not None:
        bits.append(f"净占{pct(row.main_pct)}")
    if row.streak_days:
        bits.append(f"streak {streak_text(row)}")
    if row.signal_total is not None:
        bits.append(f"score {signed(row.signal_total, 0)}")
    if row.divergence:
        bits.append(row.divergence)
    return " / ".join(bits)


def make_watch_items(rows: list[StockRow]) -> list[str]:
    items: list[str] = []
    outflows = [r for r in sorted(rows, key=lambda r: r.main_yi if r.main_yi is not None else 10**9) if r.main_yi is not None]
    inflows = [r for r in sorted_by_flow(rows) if r.main_yi is not None and r.main_yi > 0]
    price_up_flow_out = [
        r for r in rows
        if (r.chg_pct or 0) > 1 and (r.main_yi or 0) < 0
    ]
    long_out = [
        r for r in rows
        if r.streak_dir == "negative" and r.streak_days >= 3
    ]
    improved = [
        r for r in rows
        if r.score_delta is not None and r.score_delta >= 2
    ]

    for row in outflows[:3]:
        items.append(f"{row.name} {row.symbol}: {row_reason(row)}。先看流出是否收敛，再看价格能否止跌。")
    for row in price_up_flow_out[:2]:
        items.append(f"{row.name} {row.symbol}: 股价上涨但资金流出，属于价量资金背离，重点验证是否只是短线脉冲。")
    for row in long_out[:2]:
        items.append(f"{row.name} {row.symbol}: 连续流出 {row.streak_days} 天，若没有回补信号，仍按风险项跟踪。")
    for row in inflows[:2]:
        items.append(f"{row.name} {row.symbol}: 资金净流入靠前，观察是否能连续两天以上承接，而不是单日脉冲。")
    for row in sorted(improved, key=lambda r: r.score_delta or 0, reverse=True)[:2]:
        items.append(f"{row.name} {row.symbol}: 评分较上一日改善 {signed(row.score_delta, 0)}，可作为修复候选复盘。")

    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        symbol = item.split(":", 1)[0]
        if symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(item)
    return deduped[:8]


def build_report(
    repo_root: Path,
    target_dir: Path,
    prev_dir_path: Path | None,
    rows: list[StockRow],
    snapshots: dict[str, dict[str, Any]],
) -> str:
    target_tag = target_dir.name
    prev_tag = prev_dir_path.name if prev_dir_path else None
    market = market_summary(target_dir)
    sectors = sector_rows(repo_root, target_tag, prev_tag)
    counts = tier_counts(rows)
    sources = source_counts(rows)
    errors = error_counts(snapshots)

    valid_flows = [r for r in rows if r.main_yi is not None]
    strong_flows = [r for r in valid_flows if r.source in STRONG_FUND_FLOW_SOURCES]
    strong_coverage = (len(strong_flows) / len(rows) * 100) if rows else 0
    conclusion_strength = "强" if len(strong_flows) == len(rows) and rows else (
        "混合" if strong_flows else "弱"
    )
    total_flow = sum(r.main_yi or 0 for r in valid_flows)
    pos = sum(1 for r in valid_flows if (r.main_yi or 0) > 0)
    neg = sum(1 for r in valid_flows if (r.main_yi or 0) < 0)
    missing_flow = len(rows) - len(valid_flows)
    top_in = [r for r in sorted_by_flow(rows) if r.main_yi is not None and r.main_yi > 0]
    top_out = [r for r in sorted(rows, key=lambda r: r.main_yi if r.main_yi is not None else 10**9) if r.main_yi is not None and r.main_yi < 0]
    sector_in = next((s for s in sectors if (s["current"] or 0) > 0), None)
    sector_out = next((s for s in sectors if (s["current"] or 0) < 0), None)

    title = f"# A股 Watchlist 自动分析 - {zh_date(target_tag)}"
    if prev_tag:
        title += f"（接 {zh_date(prev_tag)}）"
    lines = [
        title,
        "",
        "> 数据源：akshare/local snapshots。本报告仅做信息整理和复盘，不构成投资建议。",
        "",
        "## 一句话结论",
        "",
        (
            f"{zh_date(target_tag)} watchlist 合计主力净流{('入' if total_flow >= 0 else '出')}"
            f"{abs(total_flow):.1f}亿，{pos} 只净流入、{neg} 只净流出"
            f"{'、' + str(missing_flow) + ' 只缺资金流' if missing_flow else ''}。"
            f"评分偏多 {counts['偏多']} 只、中性 {counts['中性']} 只、警示 {counts['警示']} 只。"
            f"{('板块上，' + sector_in['sector'] + '承接最强，') if sector_in else ''}"
            f"{(sector_out['sector'] + '承压最重。') if sector_out else ''}"
            f"重点看流出榜 {short_names(top_out, 3)} 是否止血，以及流入榜 {short_names(top_in, 3)} 是否延续。"
        ),
        "",
        "## 数据质量",
        "",
        f"- snapshot：{len(snapshots)} 只；资金流可用：{len(valid_flows)} 只；缺失：{missing_flow} 只。",
        f"- 结论强度：{conclusion_strength}；EastMoney 超大单+大单强口径覆盖 {len(strong_flows)}/{len(rows)}（{strong_coverage:.0f}%）。",
        f"- 资金流来源：{', '.join(f'{k}={v}' for k, v in sources.items()) or '无'}。",
        f"- 剩余接口 warning：{', '.join(f'{k}={v}' for k, v in errors.items()) or '无'}。",
    ]

    if conclusion_strength == "强":
        lines.append("- 资金口径：本日资金流使用 EastMoney 分档口径，主力=超大单+大单，可用于较强的当日资金强弱判断。")
    elif strong_flows:
        lines.append("- 口径提醒：部分股票已使用 EastMoney 超大单+大单强口径，剩余股票仍为 fallback；跨股票强弱排序需要按来源分层看。")
    if any("ths_individual_net_today_only" in r.source for r in rows):
        lines.append("- 口径提醒：THS fallback 是同花顺个股当日净额，不等同 EastMoney 超大单+大单主力口径。它只能救场；未升级为 EM 强口径前，不输出强结论。")

    lines.extend([
        "",
        "## 资金扫描摘要",
        "",
        f"- 主要流入：{top_names(top_in, 6)}。",
        f"- 主要流出：{top_names(top_out, 6)}。",
    ])

    carry = [r for r in rows if (r.chg_pct or 0) < 0 and (r.main_yi or 0) > 0]
    price_up_out = [r for r in rows if (r.chg_pct or 0) > 0 and (r.main_yi or 0) < 0]
    if carry:
        lines.append(f"- 跌中承接：{top_names(sorted_by_flow(carry), 5)}。")
    if price_up_out:
        lines.append(f"- 价涨资金跑：{top_names(sorted(price_up_out, key=lambda r: r.chg_pct or 0, reverse=True), 5)}。")

    if sectors:
        lines.extend(["", "## 板块聚合", ""])
        body = []
        for s in sectors:
            body.append([
                str(s["sector"]),
                compact_yi(s["previous"]),
                compact_yi(s["current"]),
                compact_yi(s["delta"]),
                str(s["members"]),
                str(s["signal"]),
            ])
        lines.append(table(["板块", "上一日主力净", "本日主力净", "变化", "成员", "信号"], body))

    lines.extend(["", "## 评分梯队", ""])
    tier_body = []
    for bucket in ("偏多", "中性", "警示", "未知"):
        bucket_rows = [r for r in rows if score_bucket(r) == bucket]
        if bucket_rows:
            names = "、".join(f"{r.name}{signed(r.signal_total, 0)}" for r in sorted(bucket_rows, key=lambda r: r.signal_total or -99, reverse=True)[:8])
            tier_body.append([bucket, str(len(bucket_rows)), names])
    lines.append(table(["梯队", "数量", "代表"], tier_body))

    prev_scores = [r for r in rows if r.score_delta is not None]
    if prev_scores:
        lines.extend(["", "## 比上一交易日变化", ""])
        down = sorted(prev_scores, key=lambda r: r.score_delta or 0)[:5]
        up = sorted(prev_scores, key=lambda r: r.score_delta or 0, reverse=True)[:5]
        lines.append(f"- 评分改善：{', '.join(f'{r.name}{signed(r.score_delta, 0)}' for r in up if (r.score_delta or 0) > 0) or '无明显改善'}。")
        lines.append(f"- 评分转弱：{', '.join(f'{r.name}{signed(r.score_delta, 0)}' for r in down if (r.score_delta or 0) < 0) or '无明显转弱'}。")
        flow_swing = sorted(
            [r for r in rows if r.main_delta_yi is not None],
            key=lambda r: abs(r.main_delta_yi or 0),
            reverse=True,
        )[:6]
        if flow_swing:
            lines.append("- 资金变化最大：" + "、".join(f"{r.name}{compact_yi(r.main_delta_yi)}" for r in flow_swing) + "。")

    lines.extend(["", "## 全量扫描表", ""])
    table_rows = []
    for i, row in enumerate(sorted_by_flow(rows), 1):
        table_rows.append([
            str(i),
            row.symbol,
            row.name,
            row.industry,
            pct(row.month_pct),
            pct(row.chg_pct),
            yi(row.main_yi),
            pct(row.main_pct),
            streak_text(row),
            row.divergence or "N/A",
            f"{signed(row.signal_total, 0)} {score_bucket(row)}" if row.signal_total is not None else "N/A",
            row.valuation,
            signed(row.alpha, 1),
            "新" if row.new_symbol else "",
        ])
    lines.append(table(["#", "代码", "名称", "行业", "月内%", "今日%", "主力", "净占", "streak", "背离", "评分", "估值", "alpha", "新"], table_rows))

    watch_items = make_watch_items(rows)
    if watch_items:
        lines.extend(["", "## 下一交易日跟踪重点", ""])
        for idx, item in enumerate(watch_items, 1):
            lines.append(f"{idx}. {item}")

    lines.extend(["", "## 大盘背景", ""])
    pe = market.get("pe") or {}
    pb = market.get("pb") or {}
    market_bits = []
    if pe:
        market_bits.append(f"全A PE中位数 {number(pe.get('middlePETTM')):.2f}" if number(pe.get("middlePETTM")) is not None else "")
    if pb:
        market_bits.append(f"PB中位数 {number(pb.get('middlePB')):.2f}" if number(pb.get("middlePB")) is not None else "")
    if market.get("limit_up") is not None or market.get("limit_down") is not None:
        market_bits.append(f"涨停 {market.get('limit_up', 'N/A')} / 跌停 {market.get('limit_down', 'N/A')}")
    if market_bits:
        lines.append("- " + "；".join(b for b in market_bits if b) + "。")
    else:
        lines.append("- market.json 可读，但核心大盘字段不足。")
    if market.get("errors"):
        lines.append("- market warning：" + ", ".join(market["errors"].keys()) + "。")

    values = [r.signal_total for r in rows if r.signal_total is not None]
    if values:
        lines.append(f"- watchlist 评分中位数 {statistics.median(values):.1f}，均值 {statistics.mean(values):.1f}。")

    lines.extend([
        "",
        "## 合规声明",
        "",
        "本分析仅为公开数据整理和交易复盘辅助，不构成任何投资建议。A股存在政策、流动性、业绩变脸、退市和数据源口径差异风险。",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    target = find_target_dir(args)
    if not target.is_dir():
        raise SystemExit(f"ERROR: data dir not found: {target}")

    target_tag = target.name
    target_iso = iso_date(target_tag)
    prev = previous_dir(repo_root, target_tag, args.previous_date)
    prev_tag = prev.name if prev else None

    snapshots = load_snapshots(target)
    prev_snapshots = load_snapshots(prev) if prev else {}
    history = build_flow_history(repo_root, target_tag, args.lookback)

    prev_history = build_flow_history(repo_root, prev_tag, args.lookback) if prev_tag else {}
    prev_rows: dict[str, StockRow] = {}
    if prev_tag and prev:
        prev_iso = iso_date(prev_tag)
        for symbol, data in prev_snapshots.items():
            prev_rows[symbol] = stock_row(symbol, data, prev_iso, prev_tag, prev_history, {})

    rows = [
        stock_row(symbol, data, target_iso, target_tag, history, prev_rows)
        for symbol, data in snapshots.items()
    ]
    rows.sort(key=lambda r: r.symbol)

    strong_count = sum(1 for row in rows if row.source in STRONG_FUND_FLOW_SOURCES)
    if args.require_strong_fund_flow and strong_count < len(rows):
        print(
            f"ERROR: strong EM fund-flow coverage {strong_count}/{len(rows)}; "
            "refusing to generate a strong-conclusion report.",
            file=sys.stderr,
        )
        return 9

    report = build_report(repo_root, target, prev, rows, snapshots)
    output = args.output or (repo_root / "reports" / f"watchlist_{target_tag}.md")
    save_text(output, report)
    print(f"report: {output}")
    print(f"snapshots={len(snapshots)} previous={prev_tag or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
