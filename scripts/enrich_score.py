#!/usr/bin/env python3
"""Compute daily and medium-term signal scores for each watchlist snapshot.

Legacy score (kept for compatibility) combined three enriched fields:
  1. streak     (_main_flow_streak)   — main-fund direction persistence
  2. label      (_divergence)         — price-vs-flow quality
  3. valuation  (_valuation_summary)  — PE/PB cheapness

The active daily score is now focused on short-term follow-through:
  1. flow level         — today's main-fund net share
  2. flow change        — improvement/deterioration versus previous flow row
  3. sector breadth     — watchlist industry flow and breadth
  4. divergence risk    — price-vs-flow support or decoupling
  5. relative strength  — stock alpha vs sector proxy

MUST run AFTER enrich_streak / enrich_divergence / enrich_valuation.

Legacy scoring:
  streak:    +2 (>=3d in) / +1 (1-2d in) / 0 (flat) / -1 (1-2d out) / -2 (>=3d out)
  label:     +1 (均衡/吸筹) / 0 (漂移) / -2 (拥挤/恐慌)
  valuation: +1 (PE<40) / 0 (PE 40-100) / -1 (PE>100 or PB-only/loss)

Output:
  - `_legacy_signal_score`: old 3-factor score for continuity
  - `_daily_signal_score`: daily factor score used by future reports
  - `_medium_term_score`: non-daily fundamental background score
  - `_signal_score`: alias to `_daily_signal_score` for workflow post-flight

Usage:
    python scripts/enrich_score.py
    python scripts/enrich_score.py --date 20260519
"""
import argparse
import glob
import json
import os
import sys
from datetime import date
from pathlib import Path


DATE_KEY = "日期"
MAIN_NET_KEY = "主力净流入-净额"
MAIN_PCT_KEY = "主力净流入-净占比"
CHG_KEY = "涨跌幅"


WATCHLIST_INDUSTRY_FALLBACK = {
    "000021": "半导体",
    "000988": "通信设备",
    "001270": "半导体",
    "002156": "半导体",
    "002281": "通信设备",
    "002475": "消费电子",
    "002837": "电源设备",
    "300395": "航空装备Ⅱ",
    "300408": "元件",
    "300458": "半导体",
    "300499": "电源设备",
    "600118": "航天航空",
    "600487": "通信设备",
    "600522": "通信设备",
    "600584": "半导体",
    "600845": "软件开发",
    "600879": "航天航空",
    "601138": "通信设备",
    "601869": "通信设备",
    "603005": "半导体",
    "603256": "电子元件",
    "603773": "光学光电子",
    "603986": "半导体",
    "688008": "半导体",
    "688046": "医疗服务",
    "688047": "半导体",
    "688123": "半导体",
    "688206": "半导体",
    "688208": "计算机设备",
    "688332": "半导体",
    "688347": "半导体",
    "688380": "半导体",
    "688521": "半导体",
    "688550": "电子化学品Ⅱ",
    "688627": "通用设备",
    "688728": "半导体",
    "688981": "半导体",
}


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "None", "nan", "NaN", "null"}:
        return None
    if text.endswith("%"):
        text = text[:-1]
    multiplier = 1.0
    for suffix, factor in (("万亿", 1e12), ("亿", 1e8), ("万", 1e4), ("千", 1e3)):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            multiplier = factor
            break
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def data_dirs(repo_root):
    data_root = Path(repo_root) / "data"
    if not data_root.exists():
        return []
    return sorted(
        p for p in data_root.iterdir()
        if p.is_dir() and p.name.isdigit() and len(p.name) == 8
    )


def previous_dir(repo_root, current_tag):
    prev = [p for p in data_dirs(repo_root) if p.name < current_tag]
    return prev[-1] if prev else None


def row_date(row):
    return str(row.get(DATE_KEY) or row.get("date") or "")[:10]


def flow_rows(data, target_iso):
    rows = [
        r for r in data.get("sentiment", {}).get("fund_flow_recent_20d") or []
        if isinstance(r, dict) and row_date(r) and row_date(r) <= target_iso
    ]
    return sorted(rows, key=row_date)


def current_flow(data, target_iso):
    rows = [r for r in flow_rows(data, target_iso) if row_date(r) == target_iso]
    return rows[-1] if rows else None


def previous_flow(data, target_iso):
    rows = [r for r in flow_rows(data, target_iso) if row_date(r) < target_iso]
    return rows[-1] if rows else None


def industry_name(symbol, data):
    basic = data.get("fundamentals", {}).get("basic_info", {})
    sector = data.get("sector", {})
    return (
        basic.get("行业")
        or sector.get("industry_em")
        or sector.get("industry_xq")
        or sector.get("industry_hardcoded")
        or WATCHLIST_INDUSTRY_FALLBACK.get(symbol)
        or "未分类"
    )


def latest_financial(data):
    rows = data.get("fundamentals", {}).get("financials_absolute_recent") or []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return {}
    return sorted(rows, key=lambda r: str(r.get("报告期") or ""))[-1]


def price_amount_ratio(data, target_iso):
    rows = [
        r for r in data.get("sentiment", {}).get("price_recent") or []
        if isinstance(r, dict) and str(r.get("date") or r.get("日期") or "")[:10] <= target_iso
    ]
    rows = sorted(rows, key=lambda r: str(r.get("date") or r.get("日期") or ""))
    if len(rows) < 6:
        return None
    today_amount = number(rows[-1].get("amount") or rows[-1].get("成交额"))
    if today_amount is None:
        return None
    base = [
        number(r.get("amount") or r.get("成交额"))
        for r in rows[-6:-1]
    ]
    base = [v for v in base if v and v > 0]
    if not base:
        return None
    return today_amount / (sum(base) / len(base))


def compute_sector_stats(items, target_iso):
    sectors = {}
    for symbol, data in items:
        flow = current_flow(data, target_iso)
        if not flow:
            continue
        main_net = number(flow.get(MAIN_NET_KEY))
        if main_net is None:
            continue
        industry = industry_name(symbol, data)
        bucket = sectors.setdefault(industry, {"main_net_total_yi": 0.0, "positive": 0, "members": 0})
        main_yi = main_net / 1e8
        bucket["main_net_total_yi"] += main_yi
        bucket["positive"] += 1 if main_yi > 0 else 0
        bucket["members"] += 1
    for bucket in sectors.values():
        members = bucket.get("members") or 0
        bucket["positive_ratio"] = bucket["positive"] / members if members else None
        bucket["main_net_total_yi"] = round(bucket["main_net_total_yi"], 4)
    return sectors


def load_snapshot_items(data_dir):
    items = []
    for snap in sorted(glob.glob(os.path.join(str(data_dir), "*_snapshot.json"))):
        symbol = os.path.basename(snap)[:6]
        items.append((symbol, load_json(snap)))
    return items


def score_streak(streak):
    d = streak.get("direction")
    days = streak.get("days") or 0
    if d == "positive":
        return 2 if days >= 3 else 1
    if d == "negative":
        return -2 if days >= 3 else -1
    return 0


def score_label(divergence):
    return {"均衡": 1, "吸筹": 1, "漂移": 0, "拥挤": -2, "恐慌": -2}.get(
        divergence.get("interpretation", ""), 0
    )


def score_valuation(valuation):
    metric = valuation.get("primary_metric", "")
    value = valuation.get("primary_value")
    if metric == "PB":
        return -1            # PE invalid => loss-making / extreme
    if value is None:
        return 0
    if value < 40:
        return 1
    if value <= 100:
        return 0
    return -1


def legacy_score(streak, divergence, valuation, target_iso):
    s1 = score_streak(streak)
    s2 = score_label(divergence)
    s3 = score_valuation(valuation)
    total = s1 + s2 + s3
    return {
        "as_of": target_iso,
        "model": "legacy_v1",
        "streak_score": s1,
        "label_score": s2,
        "valuation_score": s3,
        "total": total,
        "tier": tier(total),
        "lights": {"streak": light(s1), "label": light(s2), "valuation": light(s3)},
        "note": "旧三维信号质量评分，仅用于历史兼容；未来日报使用 _daily_signal_score",
    }


def score_flow_level(main_pct):
    if main_pct is None:
        return 0
    if main_pct >= 8:
        return 2
    if main_pct >= 3:
        return 1
    if main_pct <= -8:
        return -2
    if main_pct <= -3:
        return -1
    return 0


def score_flow_change(delta):
    if delta is None:
        return 0
    if delta >= 10:
        return 2
    if delta >= 5:
        return 1
    if delta <= -10:
        return -2
    if delta <= -5:
        return -1
    return 0


def score_sector_context(sector_now, sector_prev):
    if not sector_now:
        return 0
    total = number(sector_now.get("main_net_total_yi"))
    ratio = number(sector_now.get("positive_ratio"))
    before = number((sector_prev or {}).get("main_net_total_yi"))
    delta = total - before if total is not None and before is not None else None

    score = 0
    if total is not None and ratio is not None:
        if total > 0 and ratio >= 0.6:
            score += 1
        elif total < 0 and ratio <= 0.4:
            score -= 1
    if delta is not None:
        if delta > 0 and (total or 0) > 0:
            score += 1
        elif delta < 0 and (total or 0) < 0:
            score -= 1
    return max(-2, min(2, score))


def score_divergence_context(chg_pct, main_pct, divergence):
    score = 0
    div_score = number(divergence.get("score"))
    if chg_pct is not None and main_pct is not None:
        if chg_pct < 0 and main_pct > 0:
            score += 1
        if chg_pct > 0 and main_pct < 0:
            score -= 1
    if div_score is None:
        return score
    if abs(div_score) <= 3:
        score += 1
    elif div_score > 10:
        score -= 2
    elif div_score < -10:
        if main_pct is not None and main_pct > 0 and (chg_pct is None or chg_pct <= 1):
            score += 1
        else:
            score -= 1
    return max(-3, min(2, score))


def score_alpha(alpha_block):
    alpha = number(alpha_block.get("alpha"))
    if alpha is None:
        return 0
    if alpha >= 1:
        return 1
    if alpha <= -1:
        return -1
    return 0


def score_volume(data, target_iso, main_pct):
    ratio = price_amount_ratio(data, target_iso)
    if ratio is None or main_pct is None:
        return 0
    if ratio >= 1.8 and main_pct < 0:
        return -1
    if ratio >= 1.3 and main_pct > 0:
        return 1
    return 0


def daily_tier(total):
    if total >= 3:
        return "偏多"
    if total >= 0:
        return "中性"
    return "警示"


def daily_score(
    symbol,
    data,
    target_iso,
    sector_stats,
    prev_sector_stats,
    prev_data=None,
    prev_iso=None,
):
    flow = current_flow(data, target_iso) or {}
    if prev_data is not None and prev_iso:
        prev = current_flow(prev_data, prev_iso) or previous_flow(data, target_iso) or {}
    else:
        prev = previous_flow(data, target_iso) or {}
    main_pct = number(flow.get(MAIN_PCT_KEY))
    prev_main_pct = number(prev.get(MAIN_PCT_KEY))
    chg_pct = number(flow.get(CHG_KEY))
    delta_pct = main_pct - prev_main_pct if main_pct is not None and prev_main_pct is not None else None
    industry = industry_name(symbol, data)
    sector_now = sector_stats.get(industry) or {}
    sector_prev = prev_sector_stats.get(industry) or {}
    divergence = data.get("sentiment", {}).get("_divergence") or {}
    alpha_block = data.get("sector", {}).get("_alpha_vs_sector") or {}

    components = {
        "flow_level": score_flow_level(main_pct),
        "flow_change": score_flow_change(delta_pct),
        "sector_context": score_sector_context(sector_now, sector_prev),
        "divergence_context": score_divergence_context(chg_pct, main_pct, divergence),
        "relative_strength": score_alpha(alpha_block),
        "volume_confirmation": score_volume(data, target_iso, main_pct),
    }
    total = sum(components.values())
    return {
        "as_of": target_iso,
        "model": "daily_v2",
        **components,
        "total": total,
        "tier": daily_tier(total),
        "inputs": {
            "main_pct": main_pct,
            "main_pct_delta": delta_pct,
            "chg_pct": chg_pct,
            "industry": industry,
            "sector_main_net_total_yi": sector_now.get("main_net_total_yi"),
            "sector_positive_ratio": sector_now.get("positive_ratio"),
            "sector_main_net_delta_yi": (
                sector_now.get("main_net_total_yi") - sector_prev.get("main_net_total_yi")
                if sector_now.get("main_net_total_yi") is not None and sector_prev.get("main_net_total_yi") is not None
                else None
            ),
            "divergence_score": number(divergence.get("score")),
            "alpha": number(alpha_block.get("alpha")),
            "amount_ratio_1d_vs_5d": price_amount_ratio(data, target_iso),
        },
        "note": "日频短线评分，侧重资金改善、板块扩散、背离风险和相对强弱；非投资建议",
    }


def score_growth(latest):
    revenue = number(latest.get("营业总收入同比增长率"))
    profit = number(latest.get("扣非净利润同比增长率"))
    if profit is None:
        profit = number(latest.get("净利润同比增长率"))
    score = 0
    if revenue is not None:
        score += 1 if revenue >= 20 else (-1 if revenue < 0 else 0)
    if profit is not None:
        score += 1 if profit >= 20 else (-1 if profit < 0 else 0)
    return max(-2, min(2, score))


def score_quality(latest):
    roe = number(latest.get("净资产收益率") or latest.get("净资产收益率-摊薄"))
    cash_flow = number(latest.get("每股经营现金流"))
    score = 0
    if roe is not None:
        score += 1 if roe >= 8 else (-1 if roe < 0 else 0)
    if cash_flow is not None:
        score += 1 if cash_flow > 0 else -1
    return max(-2, min(2, score))


def score_balance_sheet(latest):
    debt = number(latest.get("资产负债率"))
    if debt is None:
        return 0
    if debt <= 40:
        return 1
    if debt >= 70:
        return -1
    return 0


def medium_tier(total):
    if total >= 3:
        return "偏强"
    if total >= 0:
        return "中性"
    return "风险"


def medium_term_score(data, valuation, target_iso):
    latest = latest_financial(data)
    components = {
        "valuation_score": score_valuation(valuation),
        "growth_score": score_growth(latest),
        "quality_score": score_quality(latest),
        "balance_sheet_score": score_balance_sheet(latest),
    }
    total = sum(components.values())
    return {
        "as_of": target_iso,
        "model": "medium_term_v1",
        **components,
        "total": total,
        "tier": medium_tier(total),
        "latest_report_period": latest.get("报告期"),
        "note": "中期背景评分，来自估值、成长、盈利质量和资产负债；不按日频预测使用",
    }


def light(score):
    return "🟢" if score > 0 else ("🔴" if score < 0 else "🟡")


def tier(total):
    if total >= 2:
        return "偏多"
    if total >= 0:
        return "中性"
    return "警示"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))
    snapshot_items = [(os.path.basename(path)[:6], load_json(path)) for path in snapshots]

    sector_stats = compute_sector_stats(snapshot_items, target_iso)
    prev_stats = {}
    prev_items = []
    prev_by_symbol = {}
    prev_iso = None
    prev = previous_dir(repo_root, current_tag)
    if prev:
        prev_iso = f"{prev.name[:4]}-{prev.name[4:6]}-{prev.name[6:8]}"
        try:
            prev_items = load_snapshot_items(prev)
            prev_by_symbol = {symbol: data for symbol, data in prev_items}
            prev_stats = compute_sector_stats(prev_items, prev_iso)
        except Exception as exc:
            print(f"  WARN: previous sector context unavailable: {exc}", file=sys.stderr)

    computed = skipped = 0
    results = []
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        d = load_json(snap)
        streak = d.get("sentiment", {}).get("_main_flow_streak")
        divergence = d.get("sentiment", {}).get("_divergence")
        valuation = d.get("fundamentals", {}).get("_valuation_summary")
        if streak is None or divergence is None or valuation is None:
            print(f"  {symbol}: skip (missing streak/divergence/valuation — run those first)")
            skipped += 1
            continue

        legacy = legacy_score(streak, divergence, valuation, target_iso)
        daily = daily_score(
            symbol,
            d,
            target_iso,
            sector_stats,
            prev_stats,
            prev_by_symbol.get(symbol),
            prev_iso,
        )
        medium = medium_term_score(d, valuation, target_iso)

        d["_legacy_signal_score"] = legacy
        d["_daily_signal_score"] = daily
        d["_medium_term_score"] = medium
        d["_signal_score"] = daily
        save_json(snap, d)
        results.append((daily["total"], symbol, daily["tier"], legacy["total"], medium["total"]))
        computed += 1

    results.sort(key=lambda x: -x[0])
    print(f"\n=== daily signal scores for {target_iso} ===")
    for total, symbol, t, legacy_total, medium_total in results:
        print(
            f"  {symbol}  daily={total:>+3}  [{t}]"
            f"  legacy={legacy_total:>+3}  medium={medium_total:>+3}"
        )
    print(f"\nDone: computed={computed}, skipped={skipped}, total={len(snapshots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
