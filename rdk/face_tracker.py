#!/usr/bin/env python3
"""
RDK USB camera face tracker for a continuous-rotation servo.

The tracker keeps the selected face near the frame center by sending short
left/right PWM pulses to a continuous-rotation servo. Runtime parameters can
be loaded from a JSON config file so the same code can move between RDK boards
without code edits.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path

from servo_control import PwmChannel


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "tracker_config.json"

CONFIG_DEFAULTS = {
    "pin": 33,
    "camera_id": 0,
    "frame_width": 640,
    "frame_height": 480,
    "stop_us": 1500,
    "left_us": 1430,
    "right_us": 1570,
    "pulse_time": 0.08,
    "pulse_cooldown": 0.20,
    "dead_zone": 0.10,
    "move_zone": 0.18,
    "lost_timeout": 1.5,
    "center_bias": 0.0,
    "estimated_deg_per_pulse": 6.0,
    "max_rotation_deg": 360.0,
    "confirm_frames": 2,
    "show_preview": False,
    "save_preview_path": "",
    "save_preview_every": 0.5,
}

FRAME_SLEEP_SECONDS = 0.03
_CV2 = None


def get_cv2():
    global _CV2
    if _CV2 is None:
        try:
            import cv2 as cv2_module
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "OpenCV is not installed. Install python3-opencv or the board's cv2 package."
            ) from exc
        _CV2 = cv2_module
    return _CV2


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
        description="Track a face with USB camera + continuous servo pulses."
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
        help="Print the merged runtime config and exit",
    )
    parser.add_argument("--pin", type=int, default=defaults["pin"], help="PWM board pin")
    parser.add_argument(
        "--camera-id",
        type=int,
        default=defaults["camera_id"],
        help="Video device id",
    )
    parser.add_argument("--frame-width", type=int, default=defaults["frame_width"])
    parser.add_argument("--frame-height", type=int, default=defaults["frame_height"])
    parser.add_argument("--stop-us", type=int, default=defaults["stop_us"])
    parser.add_argument("--left-us", type=int, default=defaults["left_us"])
    parser.add_argument("--right-us", type=int, default=defaults["right_us"])
    parser.add_argument("--pulse-time", type=float, default=defaults["pulse_time"])
    parser.add_argument(
        "--pulse-cooldown",
        type=float,
        default=defaults["pulse_cooldown"],
    )
    parser.add_argument(
        "--dead-zone",
        type=float,
        default=defaults["dead_zone"],
        help="No movement when |offset| <= dead_zone",
    )
    parser.add_argument(
        "--move-zone",
        type=float,
        default=defaults["move_zone"],
        help="Only pulse when |offset| >= move_zone",
    )
    parser.add_argument(
        "--lost-timeout",
        type=float,
        default=defaults["lost_timeout"],
        help="Stop tracking after this long without a face",
    )
    parser.add_argument(
        "--center-bias",
        type=float,
        default=defaults["center_bias"],
        help=(
            "Shift the virtual image center left/right. Positive values make the "
            "tracker treat faces slightly to the right as centered."
        ),
    )
    parser.add_argument(
        "--estimated-deg-per-pulse",
        type=float,
        default=defaults["estimated_deg_per_pulse"],
        help="Software-estimated rotation added per pulse",
    )
    parser.add_argument(
        "--max-rotation-deg",
        type=float,
        default=defaults["max_rotation_deg"],
        help="Estimated software rotation limit",
    )
    parser.add_argument(
        "--confirm-frames",
        type=int,
        default=defaults["confirm_frames"],
        help="Require this many consecutive frames before pulsing",
    )
    parser.add_argument(
        "--show-preview",
        dest="show_preview",
        action="store_true",
        default=bool(defaults["show_preview"]),
        help="Show an OpenCV preview window when a display is available",
    )
    parser.add_argument(
        "--no-show-preview",
        dest="show_preview",
        action="store_false",
        help="Disable the OpenCV preview window",
    )
    parser.add_argument(
        "--save-preview-path",
        type=str,
        default=str(defaults["save_preview_path"]),
        help="Save the latest annotated frame to this path for remote debugging",
    )
    parser.add_argument(
        "--save-preview-every",
        type=float,
        default=defaults["save_preview_every"],
        help="Seconds between saved preview frames",
    )
    return parser


def effective_config(args: argparse.Namespace) -> dict[str, object]:
    return {
        "pin": args.pin,
        "camera_id": args.camera_id,
        "frame_width": args.frame_width,
        "frame_height": args.frame_height,
        "stop_us": args.stop_us,
        "left_us": args.left_us,
        "right_us": args.right_us,
        "pulse_time": args.pulse_time,
        "pulse_cooldown": args.pulse_cooldown,
        "dead_zone": args.dead_zone,
        "move_zone": args.move_zone,
        "lost_timeout": args.lost_timeout,
        "center_bias": args.center_bias,
        "estimated_deg_per_pulse": args.estimated_deg_per_pulse,
        "max_rotation_deg": args.max_rotation_deg,
        "confirm_frames": args.confirm_frames,
        "show_preview": args.show_preview,
        "save_preview_path": args.save_preview_path,
        "save_preview_every": args.save_preview_every,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    config_path, defaults = resolve_defaults(argv)
    parser = build_parser(defaults, config_path)
    args = parser.parse_args(argv)
    args.config = str(Path(args.config).expanduser())

    if args.confirm_frames < 1:
        parser.error("--confirm-frames must be >= 1")
    if args.move_zone < args.dead_zone:
        parser.error("--move-zone must be >= --dead-zone")

    return args


def open_camera(camera_id: int, width: int, height: int) -> cv2.VideoCapture:
    cv2 = get_cv2()
    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open /dev/video{camera_id}")

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def load_detector() -> cv2.CascadeClassifier:
    cv2 = get_cv2()
    cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    detector = cv2.CascadeClassifier(cascade_path)
    if detector.empty():
        raise RuntimeError(f"Could not load face cascade: {cascade_path}")
    return detector


def choose_largest_face(faces):
    return max(faces, key=lambda item: item[2] * item[3])


def offset_to_direction(offset: float, dead_zone: float, move_zone: float) -> str | None:
    magnitude = abs(offset)
    if magnitude <= dead_zone:
        return None
    if magnitude < move_zone:
        return None
    return "left" if offset < 0 else "right"


def pulse_servo(
    pwm: PwmChannel,
    pulse_us: int,
    stop_us: int,
    pulse_time: float,
) -> None:
    pwm.set_pulse_us(pulse_us)
    time.sleep(pulse_time)
    pwm.stop(stop_us)


def draw_overlay(frame, face, offset: float | None, estimated_rotation: float):
    cv2 = get_cv2()
    annotated = frame.copy()

    if face is not None:
        x, y, w, h = face
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 220, 0), 2)
        cv2.putText(
            annotated,
            f"offset={offset:+.3f}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 220, 0),
            2,
        )
    else:
        cv2.putText(
            annotated,
            "no face",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 180, 255),
            2,
        )

    cv2.putText(
        annotated,
        f"rot~={estimated_rotation:+.1f} deg",
        (10, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 180, 0),
        2,
    )
    return annotated


def maybe_show_preview(
    frame,
    face,
    offset: float | None,
    estimated_rotation: float,
    show_preview: bool,
) -> bool:
    if not show_preview:
        return False

    cv2 = get_cv2()
    annotated = draw_overlay(frame, face, offset, estimated_rotation)
    cv2.imshow("face_tracker", annotated)
    key = cv2.waitKey(1) & 0xFF
    return key == ord("q")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.print_effective_config:
        print(json.dumps(effective_config(args), indent=2, sort_keys=True))
        return 0

    running = True

    def stop_handler(sig, frame):  # type: ignore[unused-arg]
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    pwm = PwmChannel(args.pin)
    cap = None

    try:
        cv2 = get_cv2()
        pwm.initialize(args.stop_us)
        cap = open_camera(args.camera_id, args.frame_width, args.frame_height)
        detector = load_detector()

        estimated_rotation = 0.0
        last_face_time = 0.0
        last_pulse_time = 0.0
        last_saved_preview_time = 0.0
        frame_counter = 0
        fps_window_start = time.time()
        pending_direction = None
        pending_frames = 0
        save_preview_path = Path(args.save_preview_path).expanduser() if args.save_preview_path else None

        print("=" * 56)
        print("RDK Face Tracker")
        print(f"config={args.config}")
        print(f"pin={args.pin} camera=/dev/video{args.camera_id}")
        print(
            f"stop={args.stop_us} left={args.left_us} right={args.right_us} "
            f"pulse_time={args.pulse_time:.2f}s cooldown={args.pulse_cooldown:.2f}s"
        )
        print(
            f"dead_zone={args.dead_zone:.2f} move_zone={args.move_zone:.2f} "
            f"center_bias={args.center_bias:+.3f} confirm_frames={args.confirm_frames} "
            f"max_rotation~={args.max_rotation_deg:.1f}deg"
        )
        if save_preview_path:
            print(f"save_preview_path={save_preview_path} every={args.save_preview_every:.2f}s")
        print("Press Ctrl+C to stop.")
        print("=" * 56)

        while running:
            ok, frame = cap.read()
            if not ok:
                print("[warn] failed to read frame")
                time.sleep(0.1)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(
                gray,
                scaleFactor=1.2,
                minNeighbors=5,
                minSize=(60, 60),
            )

            chosen_face = None
            offset = None
            now = time.time()

            if len(faces) > 0:
                chosen_face = choose_largest_face(faces)
                x, y, w, h = chosen_face
                frame_width = float(frame.shape[1])
                center_x = (x + (w / 2.0)) / frame_width
                offset = center_x - 0.5 - args.center_bias
                last_face_time = now

                direction = offset_to_direction(offset, args.dead_zone, args.move_zone)
                can_pulse = (now - last_pulse_time) >= args.pulse_cooldown

                if direction is None:
                    pending_direction = None
                    pending_frames = 0
                    if frame_counter % 15 == 0:
                        print(
                            f"[hold] centered offset={offset:+.3f} "
                            f"rot~={estimated_rotation:+.1f}deg"
                        )
                else:
                    if direction == pending_direction:
                        pending_frames += 1
                    else:
                        pending_direction = direction
                        pending_frames = 1

                    if (
                        pending_direction == "left"
                        and can_pulse
                        and pending_frames >= args.confirm_frames
                    ):
                        next_rotation = estimated_rotation - args.estimated_deg_per_pulse
                        if abs(next_rotation) <= args.max_rotation_deg:
                            pulse_servo(pwm, args.left_us, args.stop_us, args.pulse_time)
                            estimated_rotation = next_rotation
                            last_pulse_time = time.time()
                            print(
                                f"[move] left offset={offset:+.3f} "
                                f"frames={pending_frames} rot~={estimated_rotation:+.1f}deg"
                            )
                        else:
                            print("[limit] left pulse skipped at estimated rotation limit")
                        pending_direction = None
                        pending_frames = 0

                    elif (
                        pending_direction == "right"
                        and can_pulse
                        and pending_frames >= args.confirm_frames
                    ):
                        next_rotation = estimated_rotation + args.estimated_deg_per_pulse
                        if abs(next_rotation) <= args.max_rotation_deg:
                            pulse_servo(pwm, args.right_us, args.stop_us, args.pulse_time)
                            estimated_rotation = next_rotation
                            last_pulse_time = time.time()
                            print(
                                f"[move] right offset={offset:+.3f} "
                                f"frames={pending_frames} rot~={estimated_rotation:+.1f}deg"
                            )
                        else:
                            print("[limit] right pulse skipped at estimated rotation limit")
                        pending_direction = None
                        pending_frames = 0

                    elif frame_counter % 15 == 0:
                        print(
                            f"[wait] {pending_direction} offset={offset:+.3f} "
                            f"frames={pending_frames}/{args.confirm_frames}"
                        )

            else:
                pending_direction = None
                pending_frames = 0
                if (now - last_face_time) > args.lost_timeout and frame_counter % 20 == 0:
                    print("[idle] no face detected")

            if save_preview_path and (now - last_saved_preview_time) >= args.save_preview_every:
                annotated = draw_overlay(frame, chosen_face, offset, estimated_rotation)
                save_preview_path.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(save_preview_path), annotated)
                last_saved_preview_time = now

            if maybe_show_preview(
                frame,
                chosen_face,
                offset,
                estimated_rotation,
                args.show_preview,
            ):
                running = False

            frame_counter += 1
            elapsed = now - fps_window_start
            if elapsed >= 5.0:
                fps = frame_counter / elapsed
                print(
                    f"[info] fps={fps:.1f} frame={frame.shape[1]}x{frame.shape[0]} "
                    f"rot~={estimated_rotation:+.1f}deg"
                )
                frame_counter = 0
                fps_window_start = now

            time.sleep(FRAME_SLEEP_SECONDS)

        return 0

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            pwm.stop(args.stop_us)
        except Exception:
            pass
        try:
            pwm.disable()
        except Exception:
            pass
        if cap is not None:
            cap.release()
        if args.show_preview:
            cv2.destroyAllWindows()


if __name__ == "__main__":
    raise SystemExit(main())
