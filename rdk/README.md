# RDK 侧脚本说明

这个目录保存 `RDK X5` 侧运行的脚本、配置和安装脚本。

建议部署方式：

```bash
mkdir -p ~/face_tracker
cp -r rdk/* ~/face_tracker/
cd ~/face_tracker
```

## 文件说明

- `face_tracker.py`
  USB 摄像头人脸跟踪主程序
- `camera.py`
  `face_tracker.py` 的兼容入口
- `servo_control.py`
  舵机 PWM 控制和校准工具
- `ir_wake_bridge.py`
  红外 `DO` -> 屏幕串口桥接，同时接收屏幕触摸反向事件控制第二个舵机
- `tracker_config.json`
  摄像头跟随参数
- `ir_wake_config.json`
  红外桥接和第二舵机参数
- `install_service.sh`
  安装摄像头跟随服务
- `install_ir_wake_service.sh`
  安装红外桥接服务
- `stop_servo.sh`
  便捷停止舵机
- `DEPLOY.md`
  摄像头跟随部署说明
- `IR_WAKE_DEPLOY.md`
  红外桥接部署说明

## 快速开始

### 1. 检查环境

```bash
cd ~/face_tracker
python3 -m py_compile face_tracker.py servo_control.py camera.py ir_wake_bridge.py
python3 -c "import cv2; print(cv2.__version__)"
```

### 2. 摄像头单测

```bash
cd ~/face_tracker
sudo python3 face_tracker.py --save-preview-path ~/face_tracker/debug_latest.jpg
```

### 3. 红外单测

```bash
cd ~/face_tracker
sudo python3 ir_wake_bridge.py --serial-port /dev/ttyACM0
```

## 当前引脚约定

### 舵机

- 摄像头跟随舵机：`pin 33`
- 触摸反向控制第二舵机：`pin 32`

### 红外

- `gpio_pin = 16`
- `gpio_mode = BOARD`

## 提醒

- 连续旋转舵机不是定角度舵机，所以“低头一点 / 抬回一点”是靠短脉冲近似完成，不是绝对角度闭环
- 如果屏幕串口经常从 `/dev/ttyACM0` 变成 `/dev/ttyACM1`，先用 `ls /dev/ttyACM*` 确认实际设备号
- 如果有 `USB disconnect`、`error -71`、`disabled by hub (EMI?)`，优先排查 USB 线、Hub 和供电

## 进一步联调

完整联调步骤见：

[../docs/JOINT_DEBUG.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/docs/JOINT_DEBUG.md)
