#!/usr/bin/env python3
"""Validate china-stock-analysis daily JSON snapshots.

This script is intentionally stdlib-only so it can run before project
dependencies are installed. It accepts both current UTF-8 JSON and legacy
UTF-16 snapshots, but reports UTF-16 as a warning so new runs can be corrected.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


REQUIRED_TOP_LEVEL = ("symbol", "as_of", "fundamentals", "sentiment", "sector")
REQUIRED_NESTED = (
    ("fundamentals", "financial_indicators_recent"),
    ("fundamentals", "financials_absolute_recent"),
    ("sentiment", "price_recent"),
)


def default_data_dir() -> Path:
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "data" / date.today().strftime("%Y%m%d")


def load_json(path: Path) -> tuple[Any | None, str | None, str | None]:
    if not path.exists():
        return None, None, "missing file"
    if path.stat().st_size == 0:
        return None, None, "empty file"

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(path.read_text(encoding=encoding)), encoding, None
        except Exception as exc:  # keep trying alternate encodings
            last_error = exc
    return None, None, f"unreadable json: {type(last_error).__name__}: {last_error}"


def has_industry(sector: dict[str, Any]) -> bool:
    return bool(
        sector.get("industry_em")
        or sector.get("industry_xq")
        or sector.get("industry_hardcoded")
    )


def nested_value(obj: dict[str, Any], path: tuple[str, str]) -> Any:
    first, second = path
    parent = obj.get(first)
    if not isinstance(parent, dict):
        return None
    return parent.get(second)


def add(findings: list[dict[str, str]], level: str, path: Path, message: str) -> None:
    findings.append({"level": level, "file": str(path), "message": message})


def validate_snapshot(path: Path, findings: list[dict[str, str]]) -> bool:
    data, encoding, error = load_json(path)
    if error:
        add(findings, "critical", path, error)
        return False

    if encoding == "utf-16":
        add(findings, "warning", path, "legacy UTF-16 JSON; write future snapshots as UTF-8")

    if not isinstance(data, dict):
        add(findings, "critical", path, "snapshot root is not an object")
        return False

    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            add(findings, "critical", path, f"missing top-level key: {key}")

    for nested in REQUIRED_NESTED:
        value = nested_value(data, nested)
        if not value:
            add(findings, "warning", path, f"missing or empty {'.'.join(nested)}")

    sector = data.get("sector") if isinstance(data.get("sector"), dict) else {}
    if not has_industry(sector):
        add(findings, "warning", path, "missing industry classification")

    for part_name in ("fundamentals", "sentiment", "sector"):
        part = data.get(part_name)
        if not isinstance(part, dict):
            continue
        errors = part.get("errors")
        if isinstance(errors, dict) and errors:
            add(findings, "warning", path, f"{part_name}.errors present: {', '.join(errors.keys())}")

    return True


def validate_data_dir(data_dir: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    result: dict[str, Any] = {
        "data_dir": str(data_dir),
        "snapshot_count": 0,
        "parsed_snapshots": 0,
        "market_ok": False,
        "findings": findings,
    }

    if not data_dir.exists() or not data_dir.is_dir():
        add(findings, "critical", data_dir, "data directory does not exist")
        return result

    market = data_dir / "market.json"
    market_data, market_encoding, market_error = load_json(market)
    if market_error:
        add(findings, "critical", market, market_error)
    else:
        result["market_ok"] = True
        if market_encoding == "utf-16":
            add(findings, "warning", market, "legacy UTF-16 JSON; write future market.json as UTF-8")
        if isinstance(market_data, dict) and market_data.get("errors"):
            add(findings, "warning", market, f"market.errors present: {', '.join(market_data['errors'].keys())}")

    snapshots = sorted(data_dir.glob("*_snapshot.json"))
    result["snapshot_count"] = len(snapshots)
    if not snapshots:
        add(findings, "critical", data_dir, "no *_snapshot.json files found")

    for snapshot in snapshots:
        if validate_snapshot(snapshot, findings):
            result["parsed_snapshots"] += 1

    result["critical_count"] = sum(1 for f in findings if f["level"] == "critical")
    result["warning_count"] = sum(1 for f in findings if f["level"] == "warning")
    return result


def print_text(result: dict[str, Any]) -> None:
    print(f"Data health: {result['data_dir']}")
    print(
        "Summary: "
        f"snapshots={result['snapshot_count']} "
        f"parsed={result['parsed_snapshots']} "
        f"market_ok={result['market_ok']} "
        f"critical={result['critical_count']} "
        f"warnings={result['warning_count']}"
    )
    for finding in result["findings"]:
        print(f"[{finding['level'].upper()}] {finding['file']}: {finding['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate china-stock-analysis data snapshots")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="data/YYYYMMDD directory")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--strict", action="store_true", help="return non-zero when warnings are present")
    args = parser.parse_args()

    result = validate_data_dir(args.data_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)

    if result["critical_count"]:
        return 1
    if args.strict and result["warning_count"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
