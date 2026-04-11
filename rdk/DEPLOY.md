# RDK Face Tracker Deployment

## Files

- `face_tracker.py`: main tracker
- `servo_control.py`: servo test and calibration tool
- `tracker_config.json`: board-specific runtime parameters
- `install_service.sh`: installs a `systemd` service for auto-start

## Quick Start

1. Copy the whole folder to the target RDK board.
2. Verify Python and OpenCV:

```bash
cd ~/face_tracker
python3 -m py_compile face_tracker.py servo_control.py camera.py
python3 -c "import cv2; print(cv2.__version__)"
```

3. Edit `tracker_config.json` for the new board.

Recommended keys to tune first:

- `stop_us`
- `left_us`
- `right_us`
- `center_bias`
- `dead_zone`
- `move_zone`
- `pulse_time`
- `pulse_cooldown`

4. Print the effective runtime config:

```bash
python3 face_tracker.py --print-effective-config
```

5. Run the tracker manually:

```bash
sudo python3 face_tracker.py
```

6. Optional remote debug image:

```bash
sudo python3 face_tracker.py --save-preview-path ~/face_tracker/debug_latest.jpg
```

## Install As A Service

```bash
cd ~/face_tracker
chmod +x install_service.sh
sudo ./install_service.sh /home/sunrise/face_tracker
```

Useful commands:

```bash
sudo systemctl status rdk-face-tracker
sudo journalctl -u rdk-face-tracker -f
sudo systemctl restart rdk-face-tracker
sudo systemctl stop rdk-face-tracker
```

If you change `install_service.sh`, run it again so the generated service file is updated:

```bash
sudo ./install_service.sh /home/sunrise/face_tracker
```

## Migrate To Another RDK Board

1. Copy the folder.
2. Confirm camera path with `ls /dev/video*`.
3. Confirm PWM pin mapping with:

```bash
sudo python3 servo_control.py status --pin 33
```

4. Recalibrate stop and movement values:

```bash
sudo python3 servo_control.py stop --pin 33 --stop-us 1500
sudo python3 servo_control.py pulse left --pin 33 --stop-us 1500 --offset-us 70 --seconds 0.08
sudo python3 servo_control.py pulse right --pin 33 --stop-us 1500 --offset-us 70 --seconds 0.08
```

5. Update `tracker_config.json`.
6. Reinstall the service.
