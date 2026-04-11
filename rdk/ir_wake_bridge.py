#!/usr/bin/env python3
"""
RDK X5 infrared wake bridge for an ESP32 expression display.

Phase 1:
- Read a TCRT5000 digital output on an RDK GPIO input.
- When the beam/reflective sensor is triggered, send a wake command to ESP.

Phase 2:
- If no trigger is seen for a configurable timeout, send a sleep command.

The transport is a newline-delimited serial protocol so the ESP firmware can
stay simple: listen on USB CDC/UART and switch expressions on `WAKE` / `SLEEP`.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import sys
import time
from pathlib import Path

from servo_control import PwmChannel, direction_to_pulse

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "ir_wake_config.json"

CONFIG_DEFAULTS = {
    "gpio_pin": 16,
    "gpio_mode": "BOARD",
    "trigger_level": 0,
    "poll_interval": 0.02,
    "confirm_reads": 3,
    "sleep_timeout": 15.0,
    "serial_port": "",
    "serial_baudrate": 115200,
    "serial_timeout": 1.0,
    "serial_candidates": ["/dev/ttyACM*", "/dev/ttyUSB*"],
    "wake_command": "WAKE",
    "sleep_command": "SLEEP",
    "touch_servo_enabled": True,
    "touch_servo_pin": 32,
    "touch_servo_stop_us": 1500,
    "touch_servo_down_direction": "left",
    "touch_servo_down_offset_us": 60,
    "touch_servo_down_pulse_time": 0.06,
    "touch_servo_up_offset_us": 50,
    "touch_servo_up_pulse_time": 0.06,
    "touch_servo_cooldown": 0.30,
    "touch_servo_ignore_startup_seconds": 1.0,
    "sensor_ignore_after_touch_servo_seconds": 0.8,
    "touch_servo_reverse": False,
    "touch_shy_event": "TOUCH_SHY",
    "touch_center_event": "TOUCH_CENTER",
    "skip_serial": False,
    "send_initial_sleep": False,
    "verbose": True,
    "dry_run": False,
}

_GPIO = None
_SERIAL = None


def get_gpio():
    global _GPIO
    if _GPIO is None:
        try:
            import Hobot.GPIO as gpio_module
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Hobot.GPIO is not installed. Run this script on the RDK X5 board."
            ) from exc
        _GPIO = gpio_module
    return _GPIO


def get_serial_module():
    global _SERIAL
    if _SERIAL is None:
        try:
            import serial as serial_module
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "pyserial is not installed. Install it with `sudo apt install python3-serial` "
                "or your board's equivalent package."
            ) from exc
        _SERIAL = serial_module
    return _SERIAL


def load_config(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}

    try:
        raw = json.loads(config_path.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in config file {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"Config file {config_path} must contain a JSON object.")

    unknown_keys = sorted(set(raw) - set(CONFIG_DEFAULTS))
    if unknown_keys:
        raise RuntimeError(
            f"Unknown config keys in {config_path}: {', '.join(unknown_keys)}"
        )

    return raw


def resolve_defaults(argv: list[str]) -> tuple[Path, dict[str, object]]:
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG_PATH))
    pre_args, _ = pre_parser.parse_known_args(argv)

    config_path = Path(pre_args.config).expanduser()
    defaults = dict(CONFIG_DEFAULTS)
    defaults.update(load_config(config_path))
    return config_path, defaults


def build_parser(defaults: dict[str, object], config_path: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TCRT5000 -> ESP wake/sleep bridge for RDK X5."
    )
    parser.add_argument(
        "--config",
        type=str,
        default=str(config_path),
        help=f"JSON config path, default: {config_path}",
    )
    parser.add_argument(
        "--print-effective-config",
        action="store_true",
        help="Print merged runtime config and exit",
    )
    parser.add_argument("--gpio-pin", type=int, default=defaults["gpio_pin"])
    parser.add_argument(
        "--gpio-mode",
        choices=("BOARD", "BCM"),
        default=defaults["gpio_mode"],
        help="GPIO numbering mode. BOARD is recommended for RDK 40-pin headers.",
    )
    parser.add_argument(
        "--trigger-level",
        type=int,
        choices=(0, 1),
        default=defaults["trigger_level"],
        help="Digital level that means 'sensor blocked / wake'.",
    )
    parser.add_argument("--poll-interval", type=float, default=defaults["poll_interval"])
    parser.add_argument("--confirm-reads", type=int, default=defaults["confirm_reads"])
    parser.add_argument("--sleep-timeout", type=float, default=defaults["sleep_timeout"])
    parser.add_argument(
        "--serial-port",
        type=str,
        default=str(defaults["serial_port"]),
        help="Explicit serial port, for example /dev/ttyACM0.",
    )
    parser.add_argument(
        "--serial-baudrate",
        type=int,
        default=defaults["serial_baudrate"],
    )
    parser.add_argument(
        "--serial-timeout",
        type=float,
        default=defaults["serial_timeout"],
    )
    parser.add_argument(
        "--serial-candidates",
        nargs="*",
        default=list(defaults["serial_candidates"]),
        help="Auto-detect patterns used when --serial-port is empty.",
    )
    parser.add_argument(
        "--wake-command",
        type=str,
        default=str(defaults["wake_command"]),
    )
    parser.add_argument(
        "--sleep-command",
        type=str,
        default=str(defaults["sleep_command"]),
    )
    parser.add_argument(
        "--touch-servo-enabled",
        dest="touch_servo_enabled",
        action="store_true",
        default=bool(defaults["touch_servo_enabled"]),
        help="Enable the second continuous servo controlled by screen touch events.",
    )
    parser.add_argument(
        "--no-touch-servo-enabled",
        dest="touch_servo_enabled",
        action="store_false",
        help="Disable the touch-driven second servo.",
    )
    parser.add_argument(
        "--touch-servo-pin",
        type=int,
        default=defaults["touch_servo_pin"],
        help="Board pin for the second continuous servo.",
    )
    parser.add_argument(
        "--touch-servo-stop-us",
        type=int,
        default=defaults["touch_servo_stop_us"],
    )
    parser.add_argument(
        "--touch-servo-down-direction",
        choices=("left", "right"),
        default=defaults["touch_servo_down_direction"],
        help="Which rotation direction means 'down' for the second servo.",
    )
    parser.add_argument(
        "--touch-servo-down-offset-us",
        type=int,
        default=defaults["touch_servo_down_offset_us"],
    )
    parser.add_argument(
        "--touch-servo-down-pulse-time",
        type=float,
        default=defaults["touch_servo_down_pulse_time"],
    )
    parser.add_argument(
        "--touch-servo-up-offset-us",
        type=int,
        default=defaults["touch_servo_up_offset_us"],
    )
    parser.add_argument(
        "--touch-servo-up-pulse-time",
        type=float,
        default=defaults["touch_servo_up_pulse_time"],
    )
    parser.add_argument(
        "--touch-servo-cooldown",
        type=float,
        default=defaults["touch_servo_cooldown"],
    )
    parser.add_argument(
        "--touch-servo-ignore-startup-seconds",
        type=float,
        default=defaults["touch_servo_ignore_startup_seconds"],
        help="Ignore touch-backchannel events for a short time after serial open.",
    )
    parser.add_argument(
        "--sensor-ignore-after-touch-servo-seconds",
        type=float,
        default=defaults["sensor_ignore_after_touch_servo_seconds"],
        help="Ignore IR sensor transitions briefly after the second servo moves.",
    )
    parser.add_argument(
        "--touch-servo-reverse",
        dest="touch_servo_reverse",
        action="store_true",
        default=bool(defaults["touch_servo_reverse"]),
        help="Invert left/right mapping for the touch-driven second servo.",
    )
    parser.add_argument(
        "--no-touch-servo-reverse",
        dest="touch_servo_reverse",
        action="store_false",
    )
    parser.add_argument(
        "--touch-shy-event",
        type=str,
        default=str(defaults["touch_shy_event"]),
    )
    parser.add_argument(
        "--touch-center-event",
        type=str,
        default=str(defaults["touch_center_event"]),
    )
    parser.add_argument(
        "--skip-serial",
        dest="skip_serial",
        action="store_true",
        default=bool(defaults["skip_serial"]),
        help="Read the sensor but do not open or write to the ESP serial port.",
    )
    parser.add_argument(
        "--no-skip-serial",
        dest="skip_serial",
        action="store_false",
        help="Enable serial output to the ESP.",
    )
    parser.add_argument(
        "--send-initial-sleep",
        dest="send_initial_sleep",
        action="store_true",
        default=bool(defaults["send_initial_sleep"]),
        help="Send a sleep command immediately after startup to force a known UI state.",
    )
    parser.add_argument(
        "--no-send-initial-sleep",
        dest="send_initial_sleep",
        action="store_false",
        help="Do not send an initial sleep command.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=bool(defaults["dry_run"]),
        help="Print commands instead of touching GPIO/serial devices.",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Disable dry-run mode.",
    )
    parser.add_argument(
        "--verbose",
        dest="verbose",
        action="store_true",
        default=bool(defaults["verbose"]),
    )
    parser.add_argument(
        "--quiet",
        dest="verbose",
        action="store_false",
    )
    return parser


def effective_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        "gpio_pin": args.gpio_pin,
        "gpio_mode": args.gpio_mode,
        "trigger_level": args.trigger_level,
        "poll_interval": args.poll_interval,
        "confirm_reads": args.confirm_reads,
        "sleep_timeout": args.sleep_timeout,
        "serial_port": args.serial_port,
        "serial_baudrate": args.serial_baudrate,
        "serial_timeout": args.serial_timeout,
        "serial_candidates": args.serial_candidates,
        "wake_command": args.wake_command,
        "sleep_command": args.sleep_command,
        "touch_servo_enabled": args.touch_servo_enabled,
        "touch_servo_pin": args.touch_servo_pin,
        "touch_servo_stop_us": args.touch_servo_stop_us,
        "touch_servo_down_direction": args.touch_servo_down_direction,
        "touch_servo_down_offset_us": args.touch_servo_down_offset_us,
        "touch_servo_down_pulse_time": args.touch_servo_down_pulse_time,
        "touch_servo_up_offset_us": args.touch_servo_up_offset_us,
        "touch_servo_up_pulse_time": args.touch_servo_up_pulse_time,
        "touch_servo_cooldown": args.touch_servo_cooldown,
        "touch_servo_ignore_startup_seconds": args.touch_servo_ignore_startup_seconds,
        "sensor_ignore_after_touch_servo_seconds": args.sensor_ignore_after_touch_servo_seconds,
        "touch_servo_reverse": args.touch_servo_reverse,
        "touch_shy_event": args.touch_shy_event,
        "touch_center_event": args.touch_center_event,
        "skip_serial": args.skip_serial,
        "send_initial_sleep": args.send_initial_sleep,
        "verbose": args.verbose,
        "dry_run": args.dry_run,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    config_path, defaults = resolve_defaults(argv)
    parser = build_parser(defaults, config_path)
    args = parser.parse_args(argv)
    args.config = str(Path(args.config).expanduser())

    if args.confirm_reads < 1:
        parser.error("--confirm-reads must be >= 1")
    if args.sleep_timeout < 0:
        parser.error("--sleep-timeout must be >= 0")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be > 0")

    return args


def resolve_serial_port(serial_port: str, candidate_patterns: list[str]) -> str:
    if serial_port:
        return serial_port

    resolved: list[str] = []
    for pattern in candidate_patterns:
        resolved.extend(sorted(glob.glob(pattern)))

    if not resolved:
        raise RuntimeError(
            "Could not find an ESP serial port. Connect the ESP board and check /dev/ttyACM* or /dev/ttyUSB*."
        )

    return resolved[0]


class EspSerialBridge:
    def __init__(self, port: str, baudrate: int, timeout: float, dry_run: bool, verbose: bool):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.dry_run = dry_run
        self.verbose = verbose
        self.serial_handle = None
        self.rx_buffer = ""

    def open(self) -> None:
        if self.dry_run:
            return
        serial = get_serial_module()
        handle = serial.Serial()
        handle.port = self.port
        handle.baudrate = self.baudrate
        handle.timeout = self.timeout
        handle.rtscts = False
        handle.dsrdtr = False
        handle.exclusive = False
        handle.open()
        try:
            handle.setDTR(False)
            handle.setRTS(False)
        except Exception:
            pass
        self.serial_handle = handle
        try:
            self.serial_handle.reset_input_buffer()
            self.serial_handle.reset_output_buffer()
        except Exception:
            pass
        self.rx_buffer = ""
        time.sleep(0.3)

    def close(self) -> None:
        if self.serial_handle is not None:
            self.serial_handle.close()
            self.serial_handle = None

    def send(self, command: str) -> None:
        payload = f"{command}\n"
        if self.verbose:
            print(f"[serial] -> {command}")

        if self.dry_run:
            return

        if self.serial_handle is None:
            self.open()
        assert self.serial_handle is not None
        self.serial_handle.write(payload.encode("utf-8"))
        self.serial_handle.flush()

    def read_lines(self) -> list[str]:
        if self.dry_run:
            return []

        if self.serial_handle is None:
            self.open()
        assert self.serial_handle is not None

        waiting = getattr(self.serial_handle, "in_waiting", 0)
        if waiting <= 0:
            return []

        data = self.serial_handle.read(waiting)
        if not data:
            return []

        self.rx_buffer += data.decode("utf-8", errors="ignore")
        lines = self.rx_buffer.split("\n")
        self.rx_buffer = lines.pop()

        parsed: list[str] = []
        for line in lines:
            line = line.replace("\r", "").strip()
            if line:
                parsed.append(line)
        return parsed


class InfraredSensor:
    def __init__(self, gpio_pin: int, gpio_mode: str, dry_run: bool):
        self.gpio_pin = gpio_pin
        self.gpio_mode = gpio_mode
        self.dry_run = dry_run
        self.gpio = None

    def setup(self) -> None:
        if self.dry_run:
            return

        gpio = get_gpio()
        gpio.setwarnings(False)
        gpio.setmode(gpio.BOARD if self.gpio_mode == "BOARD" else gpio.BCM)
        gpio.setup(self.gpio_pin, gpio.IN)
        self.gpio = gpio

    def read(self) -> int:
        if self.dry_run:
            return 0
        assert self.gpio is not None
        return int(self.gpio.input(self.gpio_pin))

    def cleanup(self) -> None:
        if self.gpio is not None:
            self.gpio.cleanup(self.gpio_pin)


class TouchServoController:
    def __init__(
        self,
        enabled: bool,
        pin: int,
        stop_us: int,
        down_direction: str,
        down_offset_us: int,
        down_pulse_time: float,
        up_offset_us: int,
        up_pulse_time: float,
        cooldown: float,
        ignore_startup_seconds: float,
        reverse: bool,
        shy_event: str,
        center_event: str,
        verbose: bool,
    ):
        self.enabled = enabled
        self.pin = pin
        self.stop_us = stop_us
        self.down_direction = down_direction
        self.down_offset_us = down_offset_us
        self.down_pulse_time = down_pulse_time
        self.up_offset_us = up_offset_us
        self.up_pulse_time = up_pulse_time
        self.cooldown = cooldown
        self.ignore_startup_seconds = ignore_startup_seconds
        self.reverse = reverse
        self.shy_event = shy_event.strip().upper()
        self.center_event = center_event.strip().upper()
        self.verbose = verbose
        self.pwm: PwmChannel | None = None
        self.is_down = False
        self.last_move_time = 0.0
        self.started_at = 0.0

    def setup(self) -> None:
        if not self.enabled:
            return
        self.pwm = PwmChannel(self.pin)
        self.pwm.initialize(self.stop_us)
        self.pwm.stop(self.stop_us)
        self.started_at = time.time()

    def cleanup(self) -> None:
        if self.pwm is not None:
            self.pwm.stop(self.stop_us)

    def _pulse(self, direction: str, offset_us: int, pulse_time: float) -> None:
        if self.pwm is None:
            return
        now = time.time()
        if (now - self.last_move_time) < self.cooldown:
            return

        pulse_us = direction_to_pulse(self.stop_us, direction, offset_us, self.reverse)
        self.pwm.set_pulse_us(pulse_us)
        time.sleep(pulse_time)
        self.pwm.stop(self.stop_us)
        self.last_move_time = time.time()

    def handle_line(self, line: str) -> None:
        if not self.enabled:
            return

        if (time.time() - self.started_at) < self.ignore_startup_seconds:
            return

        event = line.strip()
        if not event.startswith("EVT:"):
            return

        event_name = event[4:].strip().upper()
        if self.verbose:
            print(f"[serial] <- {event_name}")

        if (event_name == self.shy_event) and not self.is_down:
            self._pulse(self.down_direction, self.down_offset_us, self.down_pulse_time)
            self.is_down = True
            if self.verbose:
                print(f"[touch-servo] shy -> move {self.down_direction}")
            return

        if (event_name == self.center_event) and self.is_down:
            up_direction = "left" if self.down_direction == "right" else "right"
            self._pulse(up_direction, self.up_offset_us, self.up_pulse_time)
            self.is_down = False
            if self.verbose:
                print(f"[touch-servo] center -> move {up_direction}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.print_effective_config:
        print(json.dumps(effective_config(args), indent=2, sort_keys=True))
        return 0

    serial_port = "(disabled)"
    if not args.skip_serial:
        serial_port = resolve_serial_port(
            args.serial_port,
            args.serial_candidates,
        )

    running = True

    def stop_handler(sig, frame):  # type: ignore[unused-arg]
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    bridge = EspSerialBridge(
        port=serial_port,
        baudrate=args.serial_baudrate,
        timeout=args.serial_timeout,
        dry_run=args.dry_run or args.skip_serial,
        verbose=args.verbose,
    )
    sensor = InfraredSensor(
        gpio_pin=args.gpio_pin,
        gpio_mode=args.gpio_mode,
        dry_run=args.dry_run,
    )
    touch_servo = TouchServoController(
        enabled=args.touch_servo_enabled and not args.dry_run,
        pin=args.touch_servo_pin,
        stop_us=args.touch_servo_stop_us,
        down_direction=args.touch_servo_down_direction,
        down_offset_us=args.touch_servo_down_offset_us,
        down_pulse_time=args.touch_servo_down_pulse_time,
        up_offset_us=args.touch_servo_up_offset_us,
        up_pulse_time=args.touch_servo_up_pulse_time,
        cooldown=args.touch_servo_cooldown,
        ignore_startup_seconds=args.touch_servo_ignore_startup_seconds,
        reverse=args.touch_servo_reverse,
        shy_event=args.touch_shy_event,
        center_event=args.touch_center_event,
        verbose=args.verbose,
    )

    blocked_state = False
    blocked_reads = 0
    clear_reads = 0
    awake = False
    last_trigger_time = 0.0
    sensor_suppressed = False

    try:
        if not args.skip_serial:
            bridge.open()
        sensor.setup()

        print("=" * 56)
        print("RDK Infrared Wake Bridge")
        print(f"config={args.config}")
        print(
            f"gpio_pin={args.gpio_pin} mode={args.gpio_mode} trigger_level={args.trigger_level} "
            f"sleep_timeout={args.sleep_timeout:.1f}s"
        )
        print(
            f"serial_port={serial_port} baudrate={args.serial_baudrate} "
            f"wake={args.wake_command} sleep={args.sleep_command} skip_serial={args.skip_serial}"
        )
        if args.touch_servo_enabled:
            print(
                f"touch_servo=pin{args.touch_servo_pin} down={args.touch_servo_down_direction} "
                f"stop={args.touch_servo_stop_us} down_offset={args.touch_servo_down_offset_us} "
                f"down_pulse={args.touch_servo_down_pulse_time:.2f}s "
                f"up_offset={args.touch_servo_up_offset_us} "
                f"up_pulse={args.touch_servo_up_pulse_time:.2f}s"
            )
        print("Press Ctrl+C to stop.")
        print("=" * 56)

        if args.send_initial_sleep:
            bridge.send(args.sleep_command)

        touch_servo.setup()

        while running:
            for line in bridge.read_lines():
                touch_servo.handle_line(line)

            now = time.time()
            if (
                touch_servo.enabled
                and touch_servo.last_move_time > 0
                and (now - touch_servo.last_move_time) < args.sensor_ignore_after_touch_servo_seconds
            ):
                blocked_reads = 0
                clear_reads = 0
                if awake:
                    last_trigger_time = now
                if args.verbose and not sensor_suppressed:
                    print(
                        f"[sensor] ignore transitions for "
                        f"{args.sensor_ignore_after_touch_servo_seconds:.2f}s after touch-servo move"
                    )
                sensor_suppressed = True
                time.sleep(args.poll_interval)
                continue

            sensor_suppressed = False
            raw_level = sensor.read()
            is_blocked = raw_level == args.trigger_level

            if is_blocked:
                blocked_reads += 1
                clear_reads = 0
            else:
                clear_reads += 1
                blocked_reads = 0

            if is_blocked and not blocked_state and blocked_reads >= args.confirm_reads:
                blocked_state = True
                last_trigger_time = now
                if not awake:
                    bridge.send(args.wake_command)
                    awake = True
                if args.verbose:
                    print(f"[sensor] blocked raw={raw_level} -> awake")

            elif not is_blocked and blocked_state and clear_reads >= args.confirm_reads:
                blocked_state = False
                if args.verbose:
                    print(f"[sensor] cleared raw={raw_level}")

            if blocked_state:
                last_trigger_time = now

            if awake and (now - last_trigger_time) >= args.sleep_timeout:
                bridge.send(args.sleep_command)
                awake = False
                if args.verbose:
                    print(f"[sleep] timeout reached ({args.sleep_timeout:.1f}s)")

            time.sleep(args.poll_interval)

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.close()
        sensor.cleanup()
        touch_servo.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
