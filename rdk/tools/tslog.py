#!/usr/bin/env python3
"""Prefix every stdin line with a Unix timestamp, for bench_report.py.

    sudo python3 -u face_tracker.py 2>&1 | python3 tools/tslog.py | tee logs/run.log

Use ``python3 -u`` on the producer, otherwise its output is block-buffered
when piped and every line gets the timestamp of the buffer flush.
"""

import sys
import time


def main() -> int:
    for line in sys.stdin:
        sys.stdout.write(f"{time.time():.3f} {line}")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
