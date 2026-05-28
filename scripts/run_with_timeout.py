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


TIMEOUT_EXIT = 124


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
    try:
        return proc.wait(timeout=args.seconds)
    except subprocess.TimeoutExpired:
        print(
            f"TIMEOUT: command exceeded {args.seconds:.0f}s: {' '.join(args.command)}",
            file=sys.stderr,
        )
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)
        except Exception:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                pass
        return TIMEOUT_EXIT


if __name__ == "__main__":
    sys.exit(main())
