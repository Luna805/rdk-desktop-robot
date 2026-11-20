from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import bench_report  # noqa: E402


def run(lines: str):
    return bench_report.analyse("t", bench_report.parse_lines(lines.strip().splitlines())).summary()


class BenchReportTest(unittest.TestCase):
    def test_settle_time_and_pulses(self):
        s = run("""
100.0 [hold] centered offset=+0.010 rot~=+0.0deg
101.0 [move] right offset=+0.300 frames=2 rot~=+6.0deg
101.5 [move] right offset=+0.200 frames=2 rot~=+12.0deg
102.0 [hold] centered offset=+0.050 rot~=+12.0deg
""")
        self.assertEqual(s["pulses"], 2)
        self.assertEqual(s["settle_episodes"], 1)
        self.assertEqual(s["settle_s_median"], 1.0)
        self.assertEqual(s["reversals"], 0)

    def test_reversal_counts_overshoot(self):
        s = run("""
10.0 [move] right offset=+0.300 frames=2 rot~=+6.0deg
10.5 [move] left offset=-0.200 frames=2 rot~=+0.0deg
13.0 [move] right offset=+0.250 frames=2 rot~=+6.0deg
""")
        self.assertEqual(s["reversals"], 1)  # the third pulse is outside the window

    def test_face_lost_drops_episode(self):
        s = run("""
1.0 [move] left offset=-0.300 frames=2 rot~=-6.0deg
2.0 [idle] no face detected
3.0 [hold] centered offset=+0.000 rot~=-6.0deg
""")
        self.assertEqual(s["settle_episodes"], 0)

    def test_journalctl_format_and_fps(self):
        s = run("""
1712000000.100000 rdk python3[812]: [info] fps=14.5 frame=640x480 rot~=+0.0deg
1712000005.100000 rdk python3[812]: [info] fps=15.5 frame=640x480 rot~=+0.0deg
""")
        self.assertEqual(s["fps_mean"], 15.0)
        self.assertEqual(s["duration_s"], 5.0)

    def test_ir_and_serial_recovery(self):
        s = run("""
1.0 [sensor] blocked raw=0 -> awake
2.0 [serial] link lost on /dev/ttyACM0: [Errno 5] Input/output error
4.5 [serial] connected on /dev/ttyACM1
20.0 [sleep] timeout reached (15.0s)
21.0 [touch-servo] shy -> move left
21.1 [touch-servo] center ignored (cooldown)
""")
        self.assertEqual(s["ir_wakes"], 1)
        self.assertEqual(s["serial_link_losses"], 1)
        self.assertEqual(s["serial_recover_s_max"], 2.5)
        self.assertEqual(s["touch_servo_moves"], 1)
        self.assertEqual(s["touch_servo_ignored"], 1)

    def test_untimestamped_lines_are_skipped(self):
        self.assertEqual(bench_report.parse_lines(["[move] left offset=-0.3"]), [])


if __name__ == "__main__":
    unittest.main()
