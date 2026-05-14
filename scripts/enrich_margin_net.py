#!/usr/bin/env python3
"""Estimate 净融资 for SZ stocks via 融资余额 day-over-day delta.

The Shenzhen exchange margin endpoint returns 融资买入额 + 融资余额 but NOT
融资偿还额, so 净融资 = 买入 - 偿还 cannot be computed directly.
Shanghai (SSE) returns all three.

Approximation: 今日净融资 ≈ 今日融资余额 - 昨日融资余额. This conflates a few
edge cases (delisted shares, accrued interest) but for the watchlist's
short-window analysis it tracks direction and order-of-magnitude reliably.

We tag the estimate explicitly so callers can distinguish from SH-direct data:
    margin_trading_today[0]["_estimated_net_margin_yi"]
    margin_trading_today[0]["_estimated_net_margin_method"] = "balance_delta"
    margin_trading_today[0]["_prev_balance_date"]            = "<YYYYMMDD>"

Skips SH stocks (which already have direct 融资偿还额) and stocks where no prior
balance is locatable.

Usage:
    python scripts/enrich_margin_net.py                # default: data/<today>
    python scripts/enrich_margin_net.py --date 20260514
"""
import argparse
import glob
import json
import os
import sys
from datetime import date


def load_json(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def is_sz(symbol: str) -> bool:
    """SZ stocks start with 0/3; SH with 6; BJ with 8/4."""
    return symbol.startswith(("0", "3"))


def find_prev_balance(symbol, repo_root, current_date_tag):
    """Walk back through historical snapshots, return (date_tag, 融资余额)."""
    data_root = os.path.join(repo_root, "data")
    if not os.path.isdir(data_root):
        return None, None
    dirs = sorted(
        (d for d in os.listdir(data_root) if d.isdigit() and len(d) == 8),
        reverse=True,
    )
    for date_tag in dirs:
        if date_tag >= current_date_tag:
            continue
        snap = os.path.join(data_root, date_tag, f"{symbol}_snapshot.json")
        if not os.path.exists(snap):
            continue
        try:
            d = load_json(snap)
        except Exception:
            continue
        mt = d.get("sentiment", {}).get("margin_trading_today") or []
        if not mt:
            continue
        bal = mt[0].get("融资余额")
        if bal:
            return date_tag, bal
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
    snapshots = sorted(glob.glob(os.path.join(target_dir, "*_snapshot.json")))

    estimated = sh_skip = no_prev = no_margin = 0
    for snap in snapshots:
        symbol = os.path.basename(snap)[:6]
        d = load_json(snap)
        mt = d.get("sentiment", {}).get("margin_trading_today") or []
        if not mt:
            print(f"  {symbol}: skip (no margin row today)")
            no_margin += 1
            continue
        m = mt[0]
        if not is_sz(symbol):
            # SH already has 融资偿还额 → direct net available, skip
            print(f"  {symbol}: skip SH (direct 融资偿还额 available)")
            sh_skip += 1
            continue
        today_bal = m.get("融资余额")
        if not today_bal:
            print(f"  {symbol}: skip (no 融资余额 in row)")
            no_margin += 1
            continue
        prev_date, prev_bal = find_prev_balance(symbol, repo_root, current_tag)
        if prev_bal is None:
            print(f"  {symbol}: skip (no prior balance found)")
            no_prev += 1
            continue
        net = (today_bal - prev_bal) / 1e8
        m["_estimated_net_margin_yi"] = round(net, 4)
        m["_estimated_net_margin_method"] = "balance_delta"
        m["_prev_balance_date"] = prev_date
        save_json(snap, d)
        print(f"  {symbol}: SZ 净融资估算 {net:+.2f} 亿 (vs {prev_date} 余额)")
        estimated += 1

    print(
        f"\nDone: SZ estimated={estimated}, SH skipped={sh_skip}, "
        f"no_prev={no_prev}, no_margin={no_margin}, total={len(snapshots)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
