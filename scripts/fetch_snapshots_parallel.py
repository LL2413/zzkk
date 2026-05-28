#!/usr/bin/env python3
"""Fetch stock snapshots concurrently with per-symbol fallback.

The shell fetcher keeps market setup and manifest creation; this helper only
handles the expensive per-symbol snapshot fan-out.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def date_tag_to_iso(tag: str) -> str | None:
    if len(tag) == 8 and tag.isdigit():
        return f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}"
    return None


def timestamp() -> str:
    return time.strftime("%H:%M:%S")


def valid_snapshot(path: Path, symbol: str, expected_date: str | None) -> bool:
    if not path.exists() or path.stat().st_size <= 1024:
        return False
    try:
        data = json.loads(path.read_text())
    except Exception:
        return False
    if data.get("symbol") != symbol:
        return False
    if expected_date and data.get("data_date") != expected_date:
        return False
    return True


def run_attempt(
    *,
    root: Path,
    py: str,
    tmp: Path,
    log_path: Path,
    symbol: str,
    timeout: int,
    force: bool,
    fast: bool,
    base_env: dict[str, str],
) -> int:
    env = base_env.copy()
    if fast:
        env.update(
            {
                "STOCK_FAST_DAILY": "1",
                "STOCK_FINANCIALS_MODE": env.get("STOCK_FINANCIALS_MODE", "auto"),
                "STOCK_SKIP_EM_FUND_FLOW_RANK": "1",
            }
        )

    cmd = [
        py,
        "scripts/run_with_timeout.py",
        str(timeout),
        py,
        "scripts/stock.py",
        "snapshot",
        symbol,
    ]
    if force:
        cmd.append("--force")
    cmd.append("--json")

    mode = "fast" if fast else "primary"
    with tmp.open("wb") as stdout, log_path.open("ab") as stderr:
        stderr.write(f"\n[{timestamp()}] {symbol} {mode} start\n".encode())
        proc = subprocess.run(cmd, cwd=root, env=env, stdout=stdout, stderr=stderr)
        stderr.write(f"[{timestamp()}] {symbol} {mode} exit={proc.returncode}\n".encode())
    return proc.returncode


def fetch_one(
    *,
    root: Path,
    out_dir: Path,
    py: str,
    symbol: str,
    expected_date: str | None,
    snapshot_timeout: int,
    fast_timeout: int,
    force: bool,
    resume_existing: bool,
    base_env: dict[str, str],
) -> tuple[str, str, int, str | None]:
    out = out_dir / f"{symbol}_snapshot.json"
    tmp = out_dir / f".{symbol}_snapshot.tmp.json"
    log_dir = out_dir / ".fetch_logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{symbol}.log"

    if resume_existing and valid_snapshot(out, symbol, expected_date):
        return symbol, "skip", out.stat().st_size, None

    if out.exists() and not valid_snapshot(out, symbol, expected_date):
        out.unlink()
    tmp.unlink(missing_ok=True)
    log_path.unlink(missing_ok=True)

    status = run_attempt(
        root=root,
        py=py,
        tmp=tmp,
        log_path=log_path,
        symbol=symbol,
        timeout=snapshot_timeout,
        force=force,
        fast=False,
        base_env=base_env,
    )
    if status != 0 or not valid_snapshot(tmp, symbol, expected_date):
        tmp.unlink(missing_ok=True)
        status = run_attempt(
            root=root,
            py=py,
            tmp=tmp,
            log_path=log_path,
            symbol=symbol,
            timeout=fast_timeout,
            force=force,
            fast=True,
            base_env=base_env,
        )

    if status == 0 and valid_snapshot(tmp, symbol, expected_date):
        size = tmp.stat().st_size
        tmp.replace(out)
        log_path.unlink(missing_ok=True)
        return symbol, "ok", size, None

    tmp.unlink(missing_ok=True)
    return symbol, "fail", 0, str(log_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-tag", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--snapshot-timeout", type=int, default=240)
    parser.add_argument("--fast-timeout", type=int, default=90)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("symbols", nargs="+")
    args = parser.parse_args()

    if args.jobs < 1:
        print("ERROR: --jobs must be >= 1", file=sys.stderr)
        return 2

    root = Path.cwd()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_date = date_tag_to_iso(args.date_tag)
    manifest = Path(args.manifest) if args.manifest else None

    def log(message: str) -> None:
        line = f"[{timestamp()}] {message}"
        print(line, flush=True)
        if manifest:
            with manifest.open("a") as fh:
                fh.write(line + "\n")

    base_env = os.environ.copy()
    base_env.setdefault("STOCK_DATE_TAG", args.date_tag)
    base_env.setdefault("STOCK_FINANCIALS_MODE", "auto")
    base_env.setdefault("STOCK_SKIP_EM_FUND_FLOW_RANK", "1")
    base_env.setdefault("STOCK_FETCH_SECTOR_FUND_FLOW", "0")

    log(
        "parallel snapshots: "
        f"jobs={args.jobs} force={int(args.force)} resume={int(args.resume_existing)} "
        f"total={len(args.symbols)}"
    )

    ok = skipped = 0
    failed: list[tuple[str, str | None]] = []
    with futures.ThreadPoolExecutor(max_workers=args.jobs) as executor:
        pending = {
            executor.submit(
                fetch_one,
                root=root,
                out_dir=out_dir,
                py=args.python,
                symbol=symbol,
                expected_date=expected_date,
                snapshot_timeout=args.snapshot_timeout,
                fast_timeout=args.fast_timeout,
                force=args.force,
                resume_existing=args.resume_existing,
                base_env=base_env,
            ): symbol
            for symbol in args.symbols
        }
        for done in futures.as_completed(pending):
            symbol, status, size, detail = done.result()
            if status == "ok":
                ok += 1
                log(f"  ok   {symbol} {size} bytes")
            elif status == "skip":
                skipped += 1
                log(f"  skip {symbol} valid existing")
            else:
                failed.append((symbol, detail))
                log(f"  FAIL {symbol} log={detail}")

    log(f"done: ok={ok} skip={skipped} fail={len(failed)} total={len(args.symbols)}")
    if failed:
        log("failed: " + " ".join(symbol for symbol, _ in failed))
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
