#!/usr/bin/env python3
"""Run a command with a hard wall-clock timeout.

This wrapper exists because some AkShare calls ignore Python socket/signal
timeouts once they are inside library internals. It starts the command in its
own process group, so a timeout can kill the whole child tree.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading


TIMEOUT_EXIT = 124


def kill_process_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGTERM)
    except Exception:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seconds", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.seconds <= 0:
        print("ERROR: timeout seconds must be positive", file=sys.stderr)
        return 2
    if not args.command:
        print("ERROR: missing command", file=sys.stderr)
        return 2

    proc = subprocess.Popen(args.command, start_new_session=True)
    timed_out = False

    def watchdog() -> None:
        nonlocal timed_out
        timer.wait(args.seconds)
        if proc.poll() is not None:
            return
        timed_out = True
        print(
            f"TIMEOUT: command exceeded {args.seconds:.0f}s: {' '.join(args.command)}",
            file=sys.stderr,
            flush=True,
        )
        kill_process_group(proc.pid)
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass

    timer = threading.Event()
    thread = threading.Thread(target=watchdog, daemon=True)
    thread.start()
    try:
        rc = proc.wait()
    finally:
        timer.set()
    if timed_out:
        return TIMEOUT_EXIT
    return rc


if __name__ == "__main__":
    sys.exit(main())
