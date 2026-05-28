#!/usr/bin/env python3
"""Aggregate sector-level fund flow from the watchlist's own per-stock data.

The EM sector_fund_flow endpoint is unreliable in some networks (consistent
ConnectionError on push2.eastmoney.com). When that fails, we can still produce
a useful sector resource by SUMMING the 主力净流入 of all watchlist members
within each industry — every member already has fund_flow_recent_20d locally.

Limitations vs. EM source:
  - Only covers industries with watchlist members (no broad-market coverage)
  - Some industries have just 1 stock → noisy; we still emit but tag count
  - Aggregation is symbol-weighted, not market-cap weighted

Output: data/<date>/sector_flow_aggregated.json
  {
    "as_of": "2026-05-14",
    "method": "watchlist_aggregation",
    "sectors": {
      "半导体": {
        "main_net_total_yi": -22.83,
        "main_net_avg_yi": -2.85,
        "member_count": 8,
        "members": ["688008", "603986", "688728", ...]
      },
      ...
    }
  }

Usage:
    python scripts/enrich_sector_flow.py                 # default: data/<today>
    python scripts/enrich_sector_flow.py --date 20260514
"""
import argparse
import glob
import json
import os
import sys
from datetime import date


# Industry assignment for watchlist symbols. Mirrors
# WATCHLIST_INDUSTRY_FALLBACK in scripts/stock.py — keep in sync.
WATCHLIST_INDUSTRY = {
    "002281": "通信设备",  "000988": "通信设备",
    "688008": "半导体",    "603986": "半导体",   "688728": "半导体",
    "688332": "半导体",    "688380": "半导体",   "688123": "半导体",
    "688046": "医疗服务",
    "688550": "化学制品",
    "688208": "计算机设备",
    "002475": "消费电子",
    "300458": "半导体",
    "601869": "通信设备",
    "600522": "通信设备",
    "600487": "通信设备",
    "300395": "半导体",
    "300408": "电子元件",
    "603256": "电子元件",
    "603773": "光学光电子",
    "000021": "半导体",
    "688627": "半导体",
    "688206": "半导体",
    "688521": "半导体",
    "688047": "半导体",
    "600845": "软件开发",
    "300499": "电源设备",
    "002837": "电源设备",
    "002156": "半导体",
    "600584": "半导体",
    "688981": "半导体",
    "688347": "半导体",
    "601138": "通信设备",
    "600118": "航天航空",
    "600879": "航天航空",
    "001270": "半导体",
}


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def get_main_net_for_date(snap_dict, target_date_iso: str):
    """Return 主力净流入-净额 (in 元) for target_date from fund_flow_recent_20d."""
    ff = snap_dict.get("sentiment", {}).get("fund_flow_recent_20d") or []
    for entry in ff:
        d = str(entry.get("日期", ""))[:10]
        if d == target_date_iso:
            return entry.get("主力净流入-净额"), entry.get("主力净流入-净占比")
    return None, None


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
    # YYYYMMDD -> YYYY-MM-DD for matching the fund_flow_recent_20d 日期 field
    target_iso = f"{current_tag[:4]}-{current_tag[4:6]}-{current_tag[6:8]}"

    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))

    # Group by industry, summing main net for the target date
    sectors: dict = {}
    missing = []
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        industry = WATCHLIST_INDUSTRY.get(symbol)
        if not industry:
            continue
        d = load_json(snap)
        net, pct = get_main_net_for_date(d, target_iso)
        if net is None:
            missing.append(symbol)
            continue
        bucket = sectors.setdefault(
            industry,
            {"main_net_total_yi": 0.0, "members": [], "pct_samples": []},
        )
        bucket["main_net_total_yi"] += net / 1e8
        bucket["members"].append(symbol)
        if pct is not None:
            bucket["pct_samples"].append(pct)

    # Finalize: avg, round, member_count
    for industry, bucket in sectors.items():
        n = len(bucket["members"])
        bucket["member_count"] = n
        bucket["main_net_avg_yi"] = round(bucket["main_net_total_yi"] / n, 4)
        bucket["main_net_total_yi"] = round(bucket["main_net_total_yi"], 4)
        if bucket["pct_samples"]:
            bucket["main_pct_avg"] = round(
                sum(bucket["pct_samples"]) / len(bucket["pct_samples"]), 4
            )
        del bucket["pct_samples"]

    out_path = os.path.join(target_dir, "sector_flow_aggregated.json")
    save_json(
        out_path,
        {
            "as_of": target_iso,
            "method": "watchlist_aggregation",
            "note": (
                "Sums 主力净流入-净额 from each watchlist member in the industry. "
                "Coverage is limited to industries with watchlist stocks; not a "
                "substitute for full-market EM sector_fund_flow_rank."
            ),
            "sectors": sectors,
            "missing_symbols": missing,
        },
    )

    # Print summary
    print(f"\n=== Sector aggregation for {target_iso} ===")
    sorted_sec = sorted(
        sectors.items(), key=lambda x: x[1]["main_net_total_yi"], reverse=True
    )
    for ind, b in sorted_sec:
        print(
            f"  {ind:12} total={b['main_net_total_yi']:+8.2f}亿  "
            f"avg={b['main_net_avg_yi']:+7.2f}亿  members={b['member_count']}"
        )
    if missing:
        print(f"\nMissing target-date fund flow for: {missing}")
    print(f"\nWrote: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
