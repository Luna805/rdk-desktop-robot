# rdk-desktop-robot

基于微雪 `ESP32-S3-Touch-LCD-4.3B` 的桌面机器人屏幕固件。

这个仓库当前聚焦屏幕端能力：

- 机器人表情显示
- 9 宫格触摸交互
- 通过串口接收外部状态
- 红外触发 `landing` 时播放旧版 `momo` 像素小人降落动画

配套的 `RDK X5` 侧脚本可以负责：

- 红外 `DO` 检测
- USB 摄像头人脸跟踪
- 舵机控制
- 通过 `Type-C` 串口给屏幕发 `landing / sleep / listening ...`

## 当前功能

- 支持状态：
  `booting`、`idle`、`listening`、`landing`、`processing`、`speaking`、`happy`、`sleep`、`error`
- 支持触摸表情：
  左看、右看、害羞、说话、睡眠、兴奋、眨眼、处理中、监听中
- 支持空闲 gaze / blink 自动行为
- 支持 `UART0` 和 `USB Serial JTAG` 两路接收状态命令
- 支持红外唤醒时的小人降落动画

## 联动架构

典型联动链路如下：

```text
TCRT5000 DO
  -> RDK X5 GPIO
  -> ir_wake_bridge.py
  -> /dev/ttyACM*
  -> robot_link
  -> emotion_engine
  -> display_service
  -> 屏幕动画/表情
```

摄像头链路与红外链路是并行的：

```text
USB Camera
  -> face_tracker.py
  -> 舵机 PWM
```

两条链路本身没有直接代码耦合，联合调试时主要关注：

- USB 串口是否稳定
- 摄像头读帧是否稳定
- RDK X5 供电与 USB Hub 是否有干扰

## 仓库结构

- `components/display/`
  屏幕、动画、触摸、`momo` 降落逻辑
- `components/emotion/`
  轻量状态机
- `components/robot_link/`
  串口状态输入
- `main/`
  主循环入口
- `emoji/`
  表情设计参考
- `docs/JOINT_DEBUG.md`
  摄像头 + 红外 + 屏幕联调步骤

## 构建与烧录

```bash
cd /path/to/rdk-desktop-robot
source ~/esp/esp-idf-v5.5.2/export.sh
idf.py build
idf.py -p /dev/cu.usbmodemXXXX flash
```

进入串口监视：

```bash
idf.py -p /dev/cu.usbmodemXXXX monitor
```

退出：

```text
Ctrl+]
```

## 屏幕端快速自测

烧录完成后，不接 RDK，也可以直接从上位机串口给屏幕发命令：

```python
import serial, time

ser = serial.Serial('/dev/cu.usbmodemXXXX', 115200, timeout=1)
time.sleep(2)
ser.write(b'landing\n')
ser.flush()
time.sleep(4)
ser.write(b'sleep\n')
ser.flush()
ser.close()
```

预期：

- `landing`：出现 `momo` 像素小人降落
- `sleep`：退出该场景

## 支持的状态命令

可直接发送：

```text
idle
listening
landing
wake
processing
speaking
happy
sleep
error
```

其中：

- `landing / wake / trigger`
  会统一映射到 `ROBOT_STATE_LANDING`
- `landing`
  用于红外唤醒时的小人降落动画

## 与 RDK X5 联调

完整联调步骤见：

[JOINT_DEBUG.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/docs/JOINT_DEBUG.md)

内容包括：

- 红外单测
- 摄像头单测
- 屏幕串口单测
- 三者同时运行
- `ttyACM*` 变化和 USB 掉线排查

## 备注

- 本仓库当前主要保存 ESP 屏幕端固件
- `RDK X5` 侧脚本如 `ir_wake_bridge.py`、`face_tracker.py` 可以放在独立目录，例如 `~/face_tracker`
- 如果你把整套桌面机器人方案继续拆仓库，推荐后续把：
  - `screen firmware`
  - `rdk supervisor`
  - `camera/servo scripts`
  分开管理
