# RDK TCRT5000 -> ESP Wake Bridge

## Goal

RDK X5 reads a TCRT5000 digital obstacle sensor and sends serial commands to
the ESP32-S3 expression board:

- blocked -> `WAKE`
- no trigger for `sleep_timeout` seconds -> `SLEEP`

## Files

- `ir_wake_bridge.py`: main bridge service
- `ir_wake_config.json`: runtime configuration
- `install_ir_wake_service.sh`: installs a `systemd` service

## Wiring Assumptions

This guide assumes a common TCRT5000 breakout with a digital output pin.

Suggested first-pass wiring:

- `TCRT5000 OUT` -> RDK GPIO input, default config uses BOARD pin `16`
- `TCRT5000 GND` -> RDK GND
- `TCRT5000 VCC` -> module's required supply
- `RDK X5` <-> `ESP32-S3-Touch-LCD-4.3B` via USB, exposing `/dev/ttyACM*`

## Important Safety Check

Before wiring `TCRT5000 OUT` directly into the RDK GPIO, measure the output
high level with a multimeter.

- If the module outputs `3.3V`, direct connection is usually OK.
- If the module outputs `5V`, do **not** connect it directly to RDK GPIO.
  Use a level shifter, divider, or optocoupler input stage.

## ESP Serial Protocol

Default commands are newline-terminated:

- `WAKE`
- `SLEEP`

If your teammate's ESP firmware expects different strings, edit:

- `wake_command`
- `sleep_command`

in `ir_wake_config.json`.

## Manual Test

```bash
cd ~/face_tracker
python3 -m py_compile ir_wake_bridge.py
python3 ir_wake_bridge.py --print-effective-config
sudo python3 ir_wake_bridge.py
```

## Sensor-Only Test

Use this before connecting the ESP board:

```bash
sudo python3 ir_wake_bridge.py --skip-serial
```

When you block the sensor, the script should print `[sensor] blocked ...`.
When you remove the obstacle, it should print `[sensor] cleared ...`.

## Dry Run

Use this when the ESP board is not connected yet:

```bash
sudo python3 ir_wake_bridge.py --dry-run
```

## Install As A Service

```bash
cd ~/face_tracker
chmod +x install_ir_wake_service.sh
sudo ./install_ir_wake_service.sh /home/sunrise/face_tracker
```

Useful commands:

```bash
sudo systemctl status rdk-ir-wake
sudo journalctl -u rdk-ir-wake -f
sudo tail -n 100 /var/log/rdk-ir-wake.log
sudo systemctl restart rdk-ir-wake
sudo systemctl stop rdk-ir-wake
```

## Recommended First Tweaks

- `gpio_pin`: change if you wire the sensor to a different RDK pin
- `trigger_level`: many TCRT5000 digital outputs are active-low, but verify
- `sleep_timeout`: how long to wait before sending `SLEEP`
- `serial_port`: set explicitly if auto-detect picks the wrong device

## Next Phase

Later, this bridge can be merged into a higher-level supervisor that also reads:

- face tracker state
- touch events
- todo UI state
- voice assistant state
