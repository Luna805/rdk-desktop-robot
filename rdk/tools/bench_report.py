#!/usr/bin/env python3
"""Score face_tracker / ir_wake_bridge logs so versions can be compared.

Accepts timestamped logs from tools/tslog.py ("<unix> <message>") or from
``journalctl -o short-unix`` ("<unix> host unit[pid]: <message>"). Pass
several files to get one column per file:

    python3 tools/bench_report.py logs/v1.0.0_step.log logs/v1.1.0_step.log
    python3 tools/bench_report.py --json logs/v1.1.0_step.log

Only log lines that exist unchanged in both v1.0.0 and v1.1.0 are relied on,
so old and new runs are scored the same way.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

LINE_RE = re.compile(r"^(?P<ts>\d+(?:\.\d+)?)\s+(?:\S+\s+\S+?:\s+)?(?P<msg>\[.*)$")
MOVE_RE = re.compile(r"^\[move\] (?P<dir>left|right)\b")
FPS_RE = re.compile(r"^\[info\] fps=(?P<fps>\d+(?:\.\d+)?)")

# Two pulses in opposite directions this close together, with no centred
# frame in between, means the controller overshot and corrected: jitter.
REVERSAL_WINDOW_S = 1.5


@dataclass
class Report:
    name: str
    duration_s: float = 0.0
    fps: list[float] = field(default_factory=list)
    pulses: int = 0
    reversals: int = 0
    settle_times: list[float] = field(default_factory=list)
    settle_pulses: list[int] = field(default_factory=list)
    ir_blocked: int = 0
    ir_sleeps: int = 0
    link_losses: int = 0
    reconnect_times: list[float] = field(default_factory=list)
    touch_moves: int = 0
    touch_ignored: int = 0

    def summary(self) -> dict[str, object]:
        minutes = self.duration_s / 60.0 if self.duration_s > 0 else 0.0

        def med(values):
            return round(statistics.median(values), 2) if values else None

        return {
            "duration_s": round(self.duration_s, 1),
            "fps_mean": round(statistics.fmean(self.fps), 1) if self.fps else None,
            "fps_min": min(self.fps) if self.fps else None,
            "pulses": self.pulses,
            "pulses_per_min": round(self.pulses / minutes, 1) if minutes else None,
            "reversals": self.reversals,
            "settle_episodes": len(self.settle_times),
            "settle_s_median": med(self.settle_times),
            "settle_s_max": round(max(self.settle_times), 2) if self.settle_times else None,
            "settle_pulses_median": med(self.settle_pulses),
            "ir_wakes": self.ir_blocked,
            "ir_sleeps": self.ir_sleeps,
            "serial_link_losses": self.link_losses,
            "serial_recoveries": len(self.reconnect_times),
            "serial_recover_s_max": round(max(self.reconnect_times), 2) if self.reconnect_times else None,
            "touch_servo_moves": self.touch_moves,
            "touch_servo_ignored": self.touch_ignored,
        }


def parse_lines(lines) -> list[tuple[float, str]]:
    events = []
    for raw in lines:
        match = LINE_RE.match(raw.strip())
        if match:
            events.append((float(match["ts"]), match["msg"]))
    return events


def analyse(name: str, events: list[tuple[float, str]]) -> Report:
    report = Report(name)
    if not events:
        return report
    report.duration_s = events[-1][0] - events[0][0]

    last_move: tuple[float, str] | None = None
    episode_start: float | None = None
    episode_pulses = 0
    link_lost_at: float | None = None

    for ts, msg in events:
        fps = FPS_RE.match(msg)
        if fps:
            report.fps.append(float(fps["fps"]))
            continue

        move = MOVE_RE.match(msg)
        if move:
            direction = move["dir"]
            report.pulses += 1
            if last_move and last_move[1] != direction and ts - last_move[0] <= REVERSAL_WINDOW_S:
                report.reversals += 1
            last_move = (ts, direction)
            if episode_start is None:
                episode_start = ts
                episode_pulses = 0
            episode_pulses += 1
            continue

        if msg.startswith("[hold]"):
            last_move = None
            if episode_start is not None:
                report.settle_times.append(ts - episode_start)
                report.settle_pulses.append(episode_pulses)
                episode_start = None
            continue

        if msg.startswith("[idle]"):
            # Face lost mid-correction: don't count it as a settle.
            last_move = None
            episode_start = None
            continue

        if msg.startswith("[sensor] blocked"):
            report.ir_blocked += 1
        elif msg.startswith("[sleep] timeout"):
            report.ir_sleeps += 1
        elif msg.startswith("[serial] link lost"):
            report.link_losses += 1
            link_lost_at = ts
        elif msg.startswith("[serial] connected") and link_lost_at is not None:
            report.reconnect_times.append(ts - link_lost_at)
            link_lost_at = None
        elif msg.startswith("[touch-servo]"):
            if "ignored" in msg or "deferred" in msg:
                report.touch_ignored += 1
            else:
                report.touch_moves += 1

    return report


def format_table(reports: list[Report]) -> str:
    summaries = [r.summary() for r in reports]
    keys = list(summaries[0].keys())
    headers = ["metric"] + [r.name for r in reports]
    rows = [[k] + ["-" if s[k] is None else str(s[k]) for s in summaries] for k in keys]
    widths = [max(len(row[i]) for row in rows + [headers]) for i in range(len(headers))]

    def fmt(row):
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    return "\n".join([fmt(headers), fmt(["-" * w for w in widths])] + [fmt(r) for r in rows])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    reports = []
    for path in args.logs:
        events = parse_lines(path.read_text(errors="ignore").splitlines())
        if not events:
            print(f"warning: no timestamped log lines in {path} (did you pipe through tools/tslog.py?)",
                  file=sys.stderr)
        reports.append(analyse(path.stem, events))

    if args.json:
        print(json.dumps({r.name: r.summary() for r in reports}, indent=2, ensure_ascii=False))
    else:
        print(format_table(reports))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
