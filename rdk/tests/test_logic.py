"""Hardware-free tests for the RDK-side control logic.

Run from the rdk/ directory:

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import face_tracker  # noqa: E402
import ir_wake_bridge  # noqa: E402
from ir_wake_bridge import Debouncer, EspSerialBridge, TouchServoController  # noqa: E402

DEAD, MOVE = 0.10, 0.18


class OffsetToDirectionTest(unittest.TestCase):
    def test_idle_inside_move_zone(self):
        self.assertIsNone(face_tracker.offset_to_direction(0.15, DEAD, MOVE))
        self.assertIsNone(face_tracker.offset_to_direction(-0.15, DEAD, MOVE))

    def test_starts_correction_outside_move_zone(self):
        self.assertEqual(face_tracker.offset_to_direction(0.25, DEAD, MOVE), "right")
        self.assertEqual(face_tracker.offset_to_direction(-0.25, DEAD, MOVE), "left")

    def test_hysteresis_keeps_correcting_until_dead_zone(self):
        # Between dead_zone and move_zone: idle when starting, but an ongoing
        # correction keeps going. This is what makes dead_zone meaningful.
        self.assertIsNone(face_tracker.offset_to_direction(0.15, DEAD, MOVE, correcting=False))
        self.assertEqual(face_tracker.offset_to_direction(0.15, DEAD, MOVE, correcting=True), "right")
        self.assertIsNone(face_tracker.offset_to_direction(0.08, DEAD, MOVE, correcting=True))


class DebouncerTest(unittest.TestCase):
    def test_needs_consecutive_reads(self):
        d = Debouncer(3)
        self.assertFalse(d.update(True))
        self.assertFalse(d.update(True))
        self.assertTrue(d.update(True))
        self.assertTrue(d.state)

    def test_glitch_resets_count(self):
        d = Debouncer(3)
        d.update(True)
        d.update(True)
        d.update(False)  # single glitch back to the stable level
        self.assertFalse(d.update(True))
        self.assertFalse(d.state)

    def test_reset_discards_partial_count(self):
        d = Debouncer(2)
        d.update(True)
        d.reset()
        self.assertFalse(d.update(True))
        self.assertTrue(d.update(True))


class FakePwm:
    def __init__(self):
        self.pulses: list[int] = []

    def set_pulse_us(self, us):
        self.pulses.append(us)

    def stop(self, us):
        pass


def make_touch_servo(cooldown=0.3):
    servo = TouchServoController(
        enabled=True, pin=32, stop_us=1500, down_direction="left",
        down_offset_us=60, down_pulse_time=0.0, up_offset_us=50, up_pulse_time=0.0,
        cooldown=cooldown, ignore_startup_seconds=0.0, reverse=False,
        shy_event="TOUCH_SHY", center_event="TOUCH_CENTER", verbose=False,
    )
    servo.pwm = FakePwm()
    return servo


class TouchServoTest(unittest.TestCase):
    def test_shy_then_center_moves_down_and_up(self):
        servo = make_touch_servo(cooldown=0.0)
        servo.handle_line("EVT:TOUCH_SHY")
        self.assertTrue(servo.is_down)
        servo.handle_line("EVT:TOUCH_CENTER")
        self.assertFalse(servo.is_down)
        self.assertEqual(servo.pwm.pulses, [1440, 1550])

    def test_pulse_skipped_by_cooldown_does_not_flip_state(self):
        # Regression: is_down used to flip even when the pulse was skipped,
        # so the next CENTER would rotate the head up past neutral.
        servo = make_touch_servo(cooldown=10.0)
        servo.handle_line("EVT:TOUCH_SHY")       # moves down
        servo.handle_line("EVT:TOUCH_CENTER")    # inside cooldown -> skipped
        self.assertTrue(servo.is_down, "state must still say 'down'")
        self.assertEqual(len(servo.pwm.pulses), 1)

    def test_ignores_non_event_lines(self):
        servo = make_touch_servo(cooldown=0.0)
        servo.handle_line("I (1234) robot_link: USB state -> idle")
        self.assertEqual(servo.pwm.pulses, [])


class FakeSerialHandle:
    def __init__(self, fail_writes=0):
        self.written: list[bytes] = []
        self.fail_writes = fail_writes
        self.in_waiting = 0

    def write(self, data):
        if self.fail_writes:
            self.fail_writes -= 1
            raise OSError(5, "Input/output error")
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        pass


class SerialReconnectTest(unittest.TestCase):
    def make_bridge(self):
        bridge = EspSerialBridge("/dev/ttyACM0", 115200, 1.0, dry_run=False, verbose=False,
                                 candidates=["/dev/ttyACM*"])
        bridge.RECONNECT_INTERVAL = 0.0
        return bridge

    def test_write_error_reconnects_and_replays_last_state(self):
        bridge = self.make_bridge()
        handles = [FakeSerialHandle(fail_writes=1), FakeSerialHandle()]

        def fake_open():
            bridge.serial_handle = handles.pop(0)
            bridge.last_opened = bridge.serial_handle

        with mock.patch.object(bridge, "open", side_effect=fake_open), \
             mock.patch.object(ir_wake_bridge, "resolve_serial_port", return_value="/dev/ttyACM1"):
            self.assertFalse(bridge.send("landing"))  # first handle dies mid-write
            self.assertIsNone(bridge.serial_handle)
            bridge.read_lines()                        # main loop tick reconnects
            self.assertEqual(bridge.port, "/dev/ttyACM1")
            self.assertEqual(bridge.last_opened.written, [b"landing\n"])

    def test_offline_send_is_delivered_once_after_reconnect(self):
        bridge = self.make_bridge()
        handle = FakeSerialHandle()
        attempts = {"n": 0}

        def flaky_open():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError(2, "No such file or directory")
            bridge.serial_handle = handle

        with mock.patch.object(bridge, "open", side_effect=flaky_open), \
             mock.patch.object(ir_wake_bridge, "resolve_serial_port", return_value="/dev/ttyACM0"):
            self.assertFalse(bridge.send("sleep"))
            self.assertTrue(bridge.send("landing"))   # reconnects, sends latest only
            self.assertEqual(handle.written, [b"landing\n"])


class ResolveSerialPortTest(unittest.TestCase):
    def test_falls_back_when_configured_port_missing(self):
        with mock.patch("os.path.exists", return_value=False), \
             mock.patch("glob.glob", side_effect=lambda pat: ["/dev/ttyACM1"] if "ACM" in pat else []):
            port = ir_wake_bridge.resolve_serial_port("/dev/ttyACM0", ["/dev/ttyACM*", "/dev/ttyUSB*"])
        self.assertEqual(port, "/dev/ttyACM1")


if __name__ == "__main__":
    unittest.main()
