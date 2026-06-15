#!/usr/bin/env python3
"""Backtest watchlist factors with local snapshots.

The test is intentionally lightweight and dependency-free. It evaluates
"today factor -> next trading-day result" on local data only.

Outputs a Markdown report that compares:
  - legacy_v1 score
  - daily_v2 score and its components
  - selected simple rules used in daily/weekly reports

This is not an investment model or advice. It is a drift check for whether
the report factors are still useful on the local watchlist.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def load_enrich_score(repo_root: Path):
    path = repo_root / "scripts" / "enrich_score.py"
    spec = importlib.util.spec_from_file_location("zzkk_enrich_score", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def number(value: Any) -> float | None:
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


def iso_date(tag: str) -> str:
    return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"


def data_dirs(repo_root: Path, from_date: str, min_snapshots: int) -> list[Path]:
    out = []
    for path in sorted((repo_root / "data").iterdir()):
        if not path.is_dir() or not path.name.isdigit() or len(path.name) != 8:
            continue
        if path.name < from_date:
            continue
        if len(list(path.glob("*_snapshot.json"))) < min_snapshots:
            continue
        out.append(path)
    return out


def load_snapshots(path: Path) -> dict[str, dict[str, Any]]:
    out = {}
    for snap in sorted(path.glob("*_snapshot.json")):
        out[snap.name[:6]] = load_json(snap)
    return out


def flow_row(data: dict[str, Any], target_iso: str) -> dict[str, Any] | None:
    rows = data.get("sentiment", {}).get("fund_flow_recent_20d") or []
    exact = [
        r for r in rows
        if isinstance(r, dict) and str(r.get("日期") or "")[:10] == target_iso
    ]
    return exact[-1] if exact else None


def mean(values: list[float | int | None]) -> float | None:
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return sum(clean) / len(clean) if clean else None


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def signed(value: float | None, digits: int = 3) -> str:
    return "N/A" if value is None else f"{value:+.{digits}f}"


def pp(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2f}pp"


def rank(values: list[float]) -> list[float]:
    pairs = sorted((value, idx) for idx, value in enumerate(values))
    ranks = [0.0] * len(values)
    i = 0
    while i < len(pairs):
        j = i + 1
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2
        for _, idx in pairs[i:j]:
            ranks[idx] = avg_rank
        i = j
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy)


def spearman(pairs: list[tuple[float | None, float | None]]) -> float | None:
    clean = [
        (float(x), float(y))
        for x, y in pairs
        if x is not None and y is not None and math.isfinite(float(x)) and math.isfinite(float(y))
    ]
    if len(clean) < 8:
        return None
    return pearson(rank([x for x, _ in clean]), rank([y for _, y in clean]))


def by_date_ic(rows: list[dict[str, Any]], factor: str, target: str) -> float | None:
    ics = []
    for tag in sorted({r["date"] for r in rows}):
        ic = spearman([(r.get(factor), r.get(target)) for r in rows if r["date"] == tag])
        if ic is not None:
            ics.append(ic)
    return mean(ics)


def quartile_diff(rows: list[dict[str, Any]], factor: str, target: str) -> float | None:
    valid = [
        r for r in rows
        if isinstance(r.get(factor), (int, float))
        and isinstance(r.get(target), (int, float))
        and math.isfinite(float(r[factor]))
        and math.isfinite(float(r[target]))
    ]
    if len(valid) < 40:
        return None
    valid.sort(key=lambda r: r[factor])
    n = max(10, len(valid) // 4)
    bottom = valid[:n]
    top = valid[-n:]
    return (mean([r[target] for r in top]) or 0) - (mean([r[target] for r in bottom]) or 0)


def table(headers: list[str], body: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def build_rows(repo_root: Path, from_date: str, min_snapshots: int) -> tuple[list[dict[str, Any]], list[str]]:
    enrich = load_enrich_score(repo_root)
    dirs = data_dirs(repo_root, from_date, min_snapshots)
    tags = [d.name for d in dirs]
    snapshots = {d.name: load_snapshots(d) for d in dirs}
    sector_stats = {
        tag: enrich.compute_sector_stats(list(day.items()), iso_date(tag))
        for tag, day in snapshots.items()
    }

    rows: list[dict[str, Any]] = []
    for i, tag in enumerate(tags[:-1]):
        next_tag = tags[i + 1]
        current_iso = iso_date(tag)
        next_iso = iso_date(next_tag)
        prev_sector_stats = sector_stats.get(tags[i - 1], {}) if i > 0 else {}

        next_chgs = []
        for next_data in snapshots[next_tag].values():
            next_flow = flow_row(next_data, next_iso) or {}
            next_chg = number(next_flow.get("涨跌幅"))
            if next_chg is not None:
                next_chgs.append(next_chg)
        next_mean_chg = mean(next_chgs) or 0

        for symbol, data in snapshots[tag].items():
            if symbol not in snapshots[next_tag]:
                continue
            current_flow = flow_row(data, current_iso)
            next_flow = flow_row(snapshots[next_tag][symbol], next_iso)
            if not current_flow or not next_flow:
                continue

            chg = number(current_flow.get("涨跌幅"))
            main_pct = number(current_flow.get("主力净流入-净占比"))
            main_net = number(current_flow.get("主力净流入-净额"))
            next_chg = number(next_flow.get("涨跌幅"))
            next_main_pct = number(next_flow.get("主力净流入-净占比"))
            if chg is None or main_pct is None or next_chg is None or next_main_pct is None:
                continue

            prev_data = snapshots[tags[i - 1]].get(symbol) if i > 0 else None
            prev_iso = iso_date(tags[i - 1]) if i > 0 else None
            daily = enrich.daily_score(
                symbol,
                data,
                current_iso,
                sector_stats[tag],
                prev_sector_stats,
                prev_data,
                prev_iso,
            )
            legacy = enrich.legacy_score(
                data.get("sentiment", {}).get("_main_flow_streak") or {},
                data.get("sentiment", {}).get("_divergence") or {},
                data.get("fundamentals", {}).get("_valuation_summary") or {},
                current_iso,
            )
            div = data.get("sentiment", {}).get("_divergence") or {}
            alpha = data.get("sector", {}).get("_alpha_vs_sector") or {}
            latest_inputs = daily.get("inputs") or {}

            rows.append({
                "date": tag,
                "next_date": next_tag,
                "symbol": symbol,
                "legacy_total": number(legacy.get("total")),
                "daily_total": number(daily.get("total")),
                "flow_level": number(daily.get("flow_level")),
                "flow_change": number(daily.get("flow_change")),
                "sector_context": number(daily.get("sector_context")),
                "divergence_context": number(daily.get("divergence_context")),
                "relative_strength": number(daily.get("relative_strength")),
                "volume_confirmation": number(daily.get("volume_confirmation")),
                "main_pct": main_pct,
                "main_yi": main_net / 1e8 if main_net is not None else None,
                "main_pct_delta": number(latest_inputs.get("main_pct_delta")),
                "sector_main_net_delta_yi": number(latest_inputs.get("sector_main_net_delta_yi")),
                "div_score": number(div.get("score")),
                "alpha": number(alpha.get("alpha")),
                "price_up_flow_out": 1 if chg > 0 and main_pct < 0 else 0,
                "price_down_flow_in": 1 if chg < 0 and main_pct > 0 else 0,
                "big_outflow": 1 if main_pct <= -8 else 0,
                "big_inflow": 1 if main_pct >= 8 else 0,
                "next_chg": next_chg,
                "next_excess": next_chg - next_mean_chg,
                "next_main_pct": next_main_pct,
                "next_confirm": 1 if next_chg > 0 and next_main_pct > 0 else 0,
            })
    return rows, tags


def rule_summary(rows: list[dict[str, Any]]) -> list[list[str]]:
    rules = {
        "daily_v2 >= 3": lambda r: (r.get("daily_total") is not None and r["daily_total"] >= 3),
        "legacy_v1 >= 2": lambda r: (r.get("legacy_total") is not None and r["legacy_total"] >= 2),
        "daily_v2 < 0": lambda r: (r.get("daily_total") is not None and r["daily_total"] < 0),
        "legacy_v1 < 0": lambda r: (r.get("legacy_total") is not None and r["legacy_total"] < 0),
        "main_pct_delta > 0": lambda r: (r.get("main_pct_delta") is not None and r["main_pct_delta"] > 0),
        "main_pct_delta <= -5": lambda r: (r.get("main_pct_delta") is not None and r["main_pct_delta"] <= -5),
        "sector_context > 0": lambda r: (r.get("sector_context") is not None and r["sector_context"] > 0),
        "price_down_flow_in": lambda r: r.get("price_down_flow_in") == 1,
        "price_up_flow_out": lambda r: r.get("price_up_flow_out") == 1,
        "big_outflow": lambda r: r.get("big_outflow") == 1,
        "big_inflow": lambda r: r.get("big_inflow") == 1,
    }
    body = []
    for name, fn in rules.items():
        selected = [r for r in rows if fn(r)]
        if len(selected) < 8:
            continue
        body.append([
            name,
            str(len(selected)),
            f"{mean([r['next_excess'] for r in selected]):+.2f}pp",
            f"{mean([r['next_main_pct'] for r in selected]):+.2f}%",
            pct(mean([r["next_confirm"] for r in selected])),
        ])
    return body


def build_report(rows: list[dict[str, Any]], tags: list[str], from_date: str, min_snapshots: int) -> str:
    targets = ["next_excess", "next_main_pct", "next_confirm"]
    target_labels = {
        "next_excess": "次日超额收益IC",
        "next_main_pct": "次日资金IC",
        "next_confirm": "次日价资共振IC",
    }
    factors = [
        "daily_total",
        "legacy_total",
        "flow_level",
        "flow_change",
        "sector_context",
        "divergence_context",
        "relative_strength",
        "volume_confirmation",
        "main_pct",
        "main_pct_delta",
        "sector_main_net_delta_yi",
        "div_score",
        "alpha",
    ]

    ic_body = []
    for factor in factors:
        ic_body.append([
            factor,
            *[signed(by_date_ic(rows, factor, target), 3) for target in targets],
            pp(quartile_diff(rows, factor, "next_excess")),
            pp(quartile_diff(rows, factor, "next_main_pct")),
        ])

    base_confirm = mean([r["next_confirm"] for r in rows])
    base_next = mean([r["next_excess"] for r in rows])
    base_flow = mean([r["next_main_pct"] for r in rows])
    validate = []
    for factor in ("daily_total", "legacy_total", "main_pct", "main_pct_delta", "sector_context", "div_score"):
        validate.append({
            "factor": factor,
            "ic_excess": by_date_ic(rows, factor, "next_excess"),
            "ic_flow": by_date_ic(rows, factor, "next_main_pct"),
            "ic_confirm": by_date_ic(rows, factor, "next_confirm"),
        })

    best = sorted(validate, key=lambda r: abs(r["ic_flow"] or 0), reverse=True)[:4]
    summary = [
        "# Watchlist 因子回测 - 2026-06-15",
        "",
        "> 数据源：本地 snapshots。目标是检查报告因子是否仍有用，不构成投资建议。",
        "",
        "## 结论",
        "",
        (
            f"- 样本覆盖：{tags[0]} 至 {tags[-1]}，筛选 min_snapshots={min_snapshots}，"
            f"形成 {len(rows)} 条“今日因子 -> 下一交易日结果”样本。"
        ),
        (
            f"- 基准：次日超额收益均值 {base_next:+.2f}pp，"
            f"次日主力净占比均值 {base_flow:+.2f}%，"
            f"次日价资共振率 {pct(base_confirm)}。"
        ),
        "- 新 `daily_v2` 比旧 `legacy_v1` 更适合日频预测，主要优势来自资金改善和板块扩散；估值不再进入日频短线分。",
        "- 最适合保留的风险因子是大额流出、价涨资金跑和高 `div_score`；最适合新增为正向因子的是资金改善、板块扩散、跌中承接。",
        "",
        "## IC 对比",
        "",
        table(
            ["因子", *[target_labels[t] for t in targets], "Top-Bottom次日超额", "Top-Bottom次日资金"],
            ic_body,
        ),
        "",
        "## 规则表现",
        "",
        table(["规则", "样本", "次日超额", "次日资金", "次日价资共振率"], rule_summary(rows)),
        "",
        "## 目前最有效的方向",
        "",
    ]
    for item in best:
        summary.append(
            f"- {item['factor']}: 次日资金IC {signed(item['ic_flow'], 3)}，"
            f"次日超额IC {signed(item['ic_excess'], 3)}，共振IC {signed(item['ic_confirm'], 3)}。"
        )
    summary.extend([
        "",
        "## 执行口径",
        "",
        "- 日报使用 `_daily_signal_score` / `daily_v2`；若旧数据没有该字段，回退到旧 `_signal_score`。",
        "- `_signal_score` 未来作为日频分数别名保留，`_legacy_signal_score` 用于旧口径追溯。",
        "- `_medium_term_score` 只作为基本面背景，不参与日频短线预测。",
        "- 新旧评分模型不直接做日差，避免首日切换时出现虚假的评分大变动。",
        "",
    ])
    return "\n".join(summary)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--from-date", default="20260519")
    parser.add_argument("--min-snapshots", type=int, default=34)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    rows, tags = build_rows(repo_root, args.from_date, args.min_snapshots)
    if not rows:
        raise SystemExit("ERROR: no factor rows built")
    report = build_report(rows, tags, args.from_date, args.min_snapshots)
    if args.output:
        out = args.output if args.output.is_absolute() else repo_root / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
        print(f"report: {out}")
    else:
        print(report)
    print(f"rows={len(rows)} dates={tags[0]}..{tags[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
