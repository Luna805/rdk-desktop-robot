#!/usr/bin/env python3
"""
Minimal RDK X5 continuous-servo controller using sysfs PWM.

This script targets the wiring that was already validated on the board:
- Pin 33 -> pwmchip for 34170000.pwm channel 1
- 50 Hz PWM
- Continuous-rotation servo with a calibrated stop pulse near 1500 us

Examples:
  sudo python3 servo_control.py status
  sudo python3 servo_control.py init --pin 33 --stop-us 1500
  sudo python3 servo_control.py spin left --pin 33 --offset-us 180 --seconds 0.20
  sudo python3 servo_control.py pulse right --pin 33 --offset-us 140 --seconds 0.12 --count 5
  sudo python3 servo_control.py calibrate --pin 33 --start-us 1450 --end-us 1550 --step-us 10
  sudo python3 servo_control.py stop --pin 33 --stop-us 1500
"""

from __future__ import annotations

import argparse
import errno
import os
import signal
import sys
import time
from pathlib import Path


PWM_ROOT = Path("/sys/class/pwm")
PWM_PERIOD_NS = 20_000_000
DEFAULT_PIN = 33
DEFAULT_STOP_US = 1500
MIN_PULSE_US = 500
MAX_PULSE_US = 2500
DEFAULT_OFFSET_US = 180

# RDK X5 mapping verified in the user's board notes.
PIN_TO_PWM = {
    27: ("34160000.pwm", 1),
    28: ("34160000.pwm", 0),
    32: ("34170000.pwm", 0),
    33: ("34170000.pwm", 1),
}


def clamp_pulse_us(value: int) -> int:
    return max(MIN_PULSE_US, min(MAX_PULSE_US, int(value)))


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("Please run with sudo so sysfs PWM can be configured.")


class PwmChannel:
    def __init__(self, board_pin: int):
        if board_pin not in PIN_TO_PWM:
            valid = ", ".join(str(pin) for pin in sorted(PIN_TO_PWM))
            raise ValueError(f"Unsupported board pin {board_pin}. Supported pins: {valid}")

        self.board_pin = board_pin
        self.device_name, self.channel = PIN_TO_PWM[board_pin]
        self.chip_path = self._resolve_chip_path()
        self.channel_path = self.chip_path / f"pwm{self.channel}"
        self.exported_here = False

    def _resolve_chip_path(self) -> Path:
        if not PWM_ROOT.exists():
            raise RuntimeError("PWM sysfs path /sys/class/pwm does not exist on this machine.")

        for chip_path in sorted(PWM_ROOT.glob("pwmchip*")):
            device_path = (chip_path / "device").resolve()
            if device_path.name == self.device_name:
                return chip_path

        raise RuntimeError(
            f"Could not find pwmchip for device {self.device_name}. "
            "Run: ls -la /sys/class/pwm/pwmchip*/device"
        )

    def _write(self, path: Path, value: int) -> None:
        path.write_text(f"{value}\n")

    def export(self) -> None:
        if self.channel_path.exists():
            return

        try:
            self._write(self.chip_path / "export", self.channel)
        except OSError as exc:
            # Some kernels report EIO/EBUSY when the channel was exported by a
            # previous run but the directory appeared slightly later.
            if exc.errno not in (errno.EBUSY, errno.EIO):
                raise

        self.exported_here = True

        deadline = time.time() + 1.0
        while time.time() < deadline:
            if self.channel_path.exists():
                return
            time.sleep(0.02)

        raise RuntimeError(f"Timed out waiting for {self.channel_path} after export.")

    def unexport(self) -> None:
        if not self.channel_path.exists():
            return
        self._write(self.chip_path / "unexport", self.channel)

    def disable(self) -> None:
        enable_path = self.channel_path / "enable"
        if enable_path.exists():
            try:
                self._write(enable_path, 0)
            except OSError as exc:
                # Some kernels reject writing 0 when the PWM is already
                # disabled or not fully initialized yet.
                if exc.errno not in (errno.EINVAL, errno.EIO):
                    raise

    def initialize(self, stop_us: int) -> None:
        self.export()

        # Many PWM drivers reject a new period when the current duty cycle is
        # non-zero or larger than the target period, so we reset duty first.
        if (self.channel_path / "enable").exists():
            self.disable()
        if (self.channel_path / "duty_cycle").exists():
            try:
                self._write(self.channel_path / "duty_cycle", 0)
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.EIO):
                    raise
            time.sleep(0.02)

        # Give the sysfs nodes a moment to settle after boot/restart.
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if (self.channel_path / "period").exists():
                break
            time.sleep(0.02)

        try:
            self._write(self.channel_path / "period", PWM_PERIOD_NS)
        except OSError as exc:
            # Retry once after a short delay for boards that are still
            # bringing the PWM controller online during early boot.
            if exc.errno not in (errno.EINVAL, errno.EIO):
                raise
            time.sleep(0.05)
            self._write(self.channel_path / "period", PWM_PERIOD_NS)
        self.set_pulse_us(stop_us)
        self._write(self.channel_path / "enable", 1)

    def set_pulse_us(self, pulse_us: int) -> None:
        self._write(self.channel_path / "duty_cycle", clamp_pulse_us(pulse_us) * 1000)

    def stop(self, stop_us: int) -> None:
        self.set_pulse_us(stop_us)

    def status_text(self) -> str:
        channel_exists = self.channel_path.exists()
        enabled = "0"
        period = "-"
        duty = "-"
        if channel_exists:
            enabled = (self.channel_path / "enable").read_text().strip()
            period = (self.channel_path / "period").read_text().strip()
            duty = (self.channel_path / "duty_cycle").read_text().strip()

        return (
            f"board_pin={self.board_pin}\n"
            f"device={self.device_name}\n"
            f"chip_path={self.chip_path}\n"
            f"channel={self.channel}\n"
            f"exported={channel_exists}\n"
            f"enabled={enabled}\n"
            f"period_ns={period}\n"
            f"duty_cycle_ns={duty}"
        )


def direction_to_pulse(stop_us: int, direction: str, offset_us: int, reverse: bool) -> int:
    sign = -1 if direction == "left" else 1
    if reverse:
        sign *= -1
    return clamp_pulse_us(stop_us + sign * offset_us)


def install_signal_stop(pwm: PwmChannel, stop_us: int, unexport_on_exit: bool) -> None:
    def handle_signal(signum, frame):  # type: ignore[unused-arg]
        try:
            pwm.stop(stop_us)
            if unexport_on_exit:
                pwm.disable()
                pwm.unexport()
        finally:
            raise SystemExit(130)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def cmd_status(args: argparse.Namespace) -> int:
    pwm = PwmChannel(args.pin)
    print(pwm.status_text())
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    pwm = PwmChannel(args.pin)
    pwm.initialize(args.stop_us)
    print(f"Initialized PWM on pin {args.pin} with stop pulse {args.stop_us} us.")
    print(pwm.status_text())
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    pwm = PwmChannel(args.pin)
    pwm.initialize(args.stop_us)
    pwm.stop(args.stop_us)
    if args.disable:
        pwm.disable()
    if args.unexport:
        pwm.unexport()

    print(
        f"Servo stopped on pin {args.pin} at {args.stop_us} us"
        + (" and disabled." if args.disable else ".")
    )
    return 0


def cmd_spin(args: argparse.Namespace) -> int:
    pwm = PwmChannel(args.pin)
    pwm.initialize(args.stop_us)
    install_signal_stop(pwm, args.stop_us, args.unexport)

    pulse_us = direction_to_pulse(args.stop_us, args.direction, args.offset_us, args.reverse)
    print(
        f"Spinning {args.direction} on pin {args.pin} with pulse {pulse_us} us "
        f"for {args.seconds:.2f} s."
    )
    pwm.set_pulse_us(pulse_us)
    time.sleep(args.seconds)
    pwm.stop(args.stop_us)

    if args.unexport:
        pwm.disable()
        pwm.unexport()

    print(f"Stopped at {args.stop_us} us.")
    return 0


def cmd_pulse(args: argparse.Namespace) -> int:
    pwm = PwmChannel(args.pin)
    pwm.initialize(args.stop_us)
    install_signal_stop(pwm, args.stop_us, args.unexport)

    pulse_us = direction_to_pulse(args.stop_us, args.direction, args.offset_us, args.reverse)
    print(
        f"Pulsing {args.direction} on pin {args.pin}: pulse={pulse_us} us, "
        f"seconds={args.seconds:.2f}, count={args.count}, cooldown={args.cooldown:.2f}"
    )

    for index in range(1, args.count + 1):
        pwm.set_pulse_us(pulse_us)
        time.sleep(args.seconds)
        pwm.stop(args.stop_us)
        print(f"Pulse {index}/{args.count} complete.")
        if index < args.count:
            time.sleep(args.cooldown)

    if args.unexport:
        pwm.disable()
        pwm.unexport()

    print(f"Stopped at {args.stop_us} us.")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    pwm = PwmChannel(args.pin)
    pwm.initialize(args.stop_us)
    install_signal_stop(pwm, args.stop_us, True)

    if args.step_us == 0:
        raise SystemExit("--step-us cannot be 0")

    step = args.step_us
    if args.start_us > args.end_us and step > 0:
        step = -step
    if args.start_us < args.end_us and step < 0:
        step = -step

    print(
        "Calibration started. Watch the servo and note the first pulse width "
        "that gives a true stop."
    )

    current = args.start_us
    target = args.end_us
    while (step > 0 and current <= target) or (step < 0 and current >= target):
        pulse_us = clamp_pulse_us(current)
        print(f"Testing {pulse_us} us for {args.hold_seconds:.2f} s...")
        pwm.set_pulse_us(pulse_us)
        time.sleep(args.hold_seconds)
        pwm.stop(args.stop_us)
        time.sleep(0.5)
        current += step

    pwm.disable()
    pwm.unexport()
    print("Calibration finished.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control a continuous-rotation servo from RDK X5 sysfs PWM."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pin", type=int, default=DEFAULT_PIN, help="Board pin, default: 33")
    common.add_argument(
        "--stop-us",
        type=int,
        default=DEFAULT_STOP_US,
        help="Stop pulse width in microseconds, default: 1500",
    )

    motion = argparse.ArgumentParser(add_help=False, parents=[common])
    motion.add_argument(
        "--offset-us",
        type=int,
        default=DEFAULT_OFFSET_US,
        help="Difference from stop pulse when moving, default: 180",
    )
    motion.add_argument(
        "--reverse",
        action="store_true",
        help="Invert left/right mapping if the current installation moves the opposite way.",
    )
    motion.add_argument(
        "--unexport",
        action="store_true",
        help="Disable and unexport the PWM channel after the command finishes.",
    )

    status_parser = subparsers.add_parser("status", parents=[common], help="Show PWM status")
    status_parser.set_defaults(func=cmd_status)

    init_parser = subparsers.add_parser("init", parents=[common], help="Export and enable PWM")
    init_parser.set_defaults(func=cmd_init)

    stop_parser = subparsers.add_parser("stop", parents=[common], help="Stop the servo")
    stop_parser.add_argument(
        "--disable",
        action="store_true",
        help="Disable PWM output after writing the stop pulse.",
    )
    stop_parser.add_argument(
        "--unexport",
        action="store_true",
        help="Unexport the PWM channel after stopping.",
    )
    stop_parser.set_defaults(func=cmd_stop)

    spin_parser = subparsers.add_parser(
        "spin",
        parents=[motion],
        help="Rotate in one direction for a fixed amount of time",
    )
    spin_parser.add_argument("direction", choices=("left", "right"))
    spin_parser.add_argument(
        "--seconds",
        type=float,
        default=0.20,
        help="How long to move before stopping, default: 0.20",
    )
    spin_parser.set_defaults(func=cmd_spin)

    pulse_parser = subparsers.add_parser(
        "pulse",
        parents=[motion],
        help="Send one or more short movement pulses",
    )
    pulse_parser.add_argument("direction", choices=("left", "right"))
    pulse_parser.add_argument(
        "--seconds",
        type=float,
        default=0.12,
        help="How long each pulse runs, default: 0.12",
    )
    pulse_parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of pulses to send, default: 1",
    )
    pulse_parser.add_argument(
        "--cooldown",
        type=float,
        default=0.30,
        help="Pause between pulses, default: 0.30",
    )
    pulse_parser.set_defaults(func=cmd_pulse)

    calibrate_parser = subparsers.add_parser(
        "calibrate",
        parents=[common],
        help="Sweep pulse widths to find the true stop pulse",
    )
    calibrate_parser.add_argument("--start-us", type=int, default=1450)
    calibrate_parser.add_argument("--end-us", type=int, default=1550)
    calibrate_parser.add_argument("--step-us", type=int, default=10)
    calibrate_parser.add_argument("--hold-seconds", type=float, default=2.0)
    calibrate_parser.set_defaults(func=cmd_calibrate)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    require_root()

    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("Interrupted.")
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
