# 摄像头 + 红外 + 屏幕联调说明

这份文档整理的是 `RDK X5 + USB 摄像头 + TCRT5000 红外 + ESP32-S3-Touch-LCD-4.3B` 的联合调试步骤。

目标是确认三件事：

1. 摄像头人脸跟踪正常
2. 红外触发屏幕动画正常
3. 两者同时运行时互不影响

## 一、系统分工

### 1. 屏幕端

屏幕固件负责：

- 接收串口状态
- 显示机器人表情
- 在收到 `landing` 时播放 `momo` 像素小人降落动画

### 2. RDK X5 端

RDK 脚本负责：

- `face_tracker.py`
  USB 摄像头人脸检测 + 舵机控制
- `ir_wake_bridge.py`
  红外 `DO` 检测 + 串口给屏幕发命令

## 二、典型接线

### 1. 红外

- `TCRT5000 DO/OUT -> RDK X5 GPIO 输入`
- `TCRT5000 GND -> RDK X5 GND`
- `TCRT5000 VCC -> 模块供电`

当前默认脚本配置：

- `gpio_pin = 16`
- `gpio_mode = BOARD`
- `trigger_level = 0`

### 2. 摄像头

- USB 摄像头 -> RDK X5 USB

当前默认配置：

- `camera_id = 0`

### 3. 屏幕

- `ESP32-S3-Touch-LCD-4.3B` -> `RDK X5`
- 使用支持数据传输的 `Type-C`
- RDK 上通常会枚举成 `/dev/ttyACM*`

## 三、联调前准备

先确保没有后台服务残留：

```bash
sudo systemctl stop rdk-ir-wake || true
sudo systemctl stop rdk-face-tracker || true
pkill -f ir_wake_bridge.py || true
pkill -f face_tracker.py || true
```

再确认设备：

```bash
ls /dev/video*
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

## 四、单项测试

### 1. 屏幕串口单测

先不跑红外脚本，直接从 RDK 发命令：

```bash
python3 - <<'PY'
import serial, time

ser = serial.Serial('/dev/ttyACM1', 115200, timeout=1)
time.sleep(2)
ser.write(b'landing\n')
ser.flush()
time.sleep(4)
ser.write(b'sleep\n')
ser.flush()
ser.close()
PY
```

预期：

- `landing`：出现 `momo` 像素小人降落
- `sleep`：退出该场景

如果无反应，先排查：

- 串口号是否变了
- Type-C 是否是数据线
- USB 是否掉线

### 2. 红外 GPIO 单测

```bash
python3 - <<'PY'
import time
import Hobot.GPIO as GPIO

pin = 16
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(pin, GPIO.IN)

print("probe BOARD 16, move your hand in/out now")
last = None
try:
    for i in range(200):
        v = GPIO.input(pin)
        if v != last:
            print(f"{i:03d} value -> {v}")
            last = v
        time.sleep(0.05)
finally:
    GPIO.cleanup(pin)
PY
```

预期：

- 遮挡 / 移开时能在 `0` 和 `1` 间切换

### 3. 红外桥接单测

```bash
cd ~/face_tracker
sudo python3 ir_wake_bridge.py --serial-port /dev/ttyACM1 --confirm-reads 5 --poll-interval 0.05 --sleep-timeout 5
```

预期：

- `[sensor] blocked`
- `[sensor] cleared`
- `[serial] -> landing`
- 屏幕掉小人

### 4. 摄像头单测

```bash
cd ~/face_tracker
sudo python3 face_tracker.py --save-preview-path ~/face_tracker/debug_latest.jpg
```

预期：

- `[move] left/right`
- `[hold] centered`
- `[idle] no face detected`
- `debug_latest.jpg` 持续更新

## 五、联合运行

开两个终端。

### 终端 A：红外

```bash
cd ~/face_tracker
sudo python3 ir_wake_bridge.py --serial-port /dev/ttyACM1 --confirm-reads 5 --poll-interval 0.05 --sleep-timeout 5
```

### 终端 B：摄像头

```bash
cd ~/face_tracker
sudo python3 face_tracker.py --save-preview-path ~/face_tracker/debug_latest.jpg
```

## 六、联调时的观察点

按下面顺序做动作：

1. 人脸左右移动，观察舵机是否跟随
2. 连续遮挡 / 放开红外 5 次，观察屏幕是否每次都掉小人
3. 红外触发时继续让摄像头看人脸，观察摄像头和舵机是否卡住

### 正常表现

- 红外触发时屏幕继续掉小人
- 摄像头日志正常输出 `[move] / [hold] / [idle]`
- 舵机动作不断
- `debug_latest.jpg` 持续更新

### 相互影响的表现

- 摄像头开始频繁 `[warn] failed to read frame`
- 舵机动作明显卡顿
- 红外明明触发了，但屏幕偶尔不响应
- `/dev/ttyACM*` 或 `/dev/video*` 设备掉线

## 七、建议同时开系统日志观察

第三个终端建议运行：

```bash
dmesg -w
```

重点留意：

- `USB disconnect`
- `disabled by hub (EMI?)`
- `error -71`

如果这些日志出现，优先排查：

- Type-C 数据线质量
- 是否经过 USB Hub
- 供电是否不足

## 八、常见问题

### 1. `/dev/ttyACM0` 变成 `/dev/ttyACM1`

很常见，直接改参数：

```bash
sudo python3 ir_wake_bridge.py --serial-port /dev/ttyACM1
```

### 2. 红外脚本报 GPIO 路径不存在

通常是：

- 有另一个红外脚本实例还在跑
- 某个后台服务占用并 cleanup 了 GPIO

先清理：

```bash
pkill -f ir_wake_bridge.py || true
sudo systemctl stop rdk-ir-wake || true
```

### 3. 屏幕单独测有反应，红外桥接没反应

优先排查：

- 串口号写错
- 红外 `DO` 没有真正切换
- `trigger_level` 不匹配
- USB 线不稳定

## 九、推荐联调顺序

每次改动后，建议按这个顺序回归：

1. 屏幕串口单测
2. 红外 GPIO 单测
3. 红外桥接单测
4. 摄像头单测
5. 同时运行联调

这样最容易定位问题到底是在：

- 屏幕
- 红外
- 摄像头
- USB/供电
- 还是多进程互相影响
