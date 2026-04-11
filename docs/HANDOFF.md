# 工程交接说明

这份文档给下一位工程师使用，目标是：

- 快速理解这个项目的边界
- 快速找到应该先看的文件
- 避免一上来被历史过程和大体量依赖淹没

## 1. 先看什么

推荐阅读顺序：

1. [README.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/README.md)
   先理解仓库里到底包含什么
2. [ARCHITECTURE.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/docs/ARCHITECTURE.md)
   再看整体系统和信号流
3. [rdk/README.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/rdk/README.md)
   了解 RDK 侧脚本如何部署和运行
4. [JOINT_DEBUG.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/docs/JOINT_DEBUG.md)
   需要联调时按这个步骤走
5. `main/app_main.c`
   看主循环
6. `components/robot_link/robot_link.c`
   看屏幕端串口输入
7. `components/emotion/emotion_engine.c`
   看状态机
8. `components/display/display_service.c`
   看屏幕、触摸、动画和触摸回传
9. `rdk/face_tracker.py`
   看摄像头跟随
10. `rdk/ir_wake_bridge.py`
    看红外桥接和触摸反向舵机逻辑

## 2. 不建议先看什么

不建议一开始就读：

- [SESSION_HANDOFF.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/SESSION_HANDOFF.md)
  这是历史开发记录，信息很多，但不适合当第一入口
- `components/esp32_display_panel/`
  这是大体量依赖和示例目录，不是当前业务主逻辑
- `managed_components/`
  第三方依赖生成目录，主要用于构建

如果要查历史决策，再回头看 `SESSION_HANDOFF.md`。

## 3. 当前项目边界

这个仓库现在已经同时包含：

### ESP 侧

- 表情屏幕固件
- GT911 触摸
- `momo` 像素小人降落动画
- 屏幕触摸事件回传

### RDK 侧

- USB 摄像头跟随
- 红外 `DO` 桥接
- 舵机 1：摄像头跟随
- 舵机 2：触摸回传动作

## 4. 当前稳定能力

截至目前，已经验证过的能力包括：

- 屏幕能正常显示表情
- 红外触发 `landing` 能掉 `momo` 小人
- 触摸表情能工作
- 屏幕触摸中上 / 中间区能回发事件给 RDK
- 第二个舵机能根据触摸事件做“低头一点 / 抬回一点”的近似动作
- 摄像头跟随脚本能独立运行

## 5. 当前实现里的关键约束

### 5.1 第二个舵机不是角度舵机

当前第二个舵机是连续旋转舵机，所以：

- 只能做短脉冲近似位移
- 不能保证每次都回到绝对一致角度

如果未来要做高精度“低头/抬头”，建议换成标准角度舵机。

### 5.2 串口双向通信要单进程独占

RDK 侧现在是同一个 `ir_wake_bridge.py` 进程同时负责：

- 发红外状态给屏幕
- 收屏幕回传事件

不要再额外起另一个进程去抢 `/dev/ttyACM*`，否则很容易出冲突。

### 5.3 USB 稳定性是项目风险点

实测里出现过：

- `USB disconnect`
- `disabled by hub (EMI?)`
- `error -71`

排查优先级：

1. 直连，不走 Hub
2. 更换 Type-C 数据线
3. 确保供电稳定

## 6. 当前默认引脚

### RDK

- 摄像头跟随舵机：`pin 33`
- 触摸反向控制舵机：`pin 32`
- 红外：`BOARD pin 16`

### 屏幕

- 串口接收：`USB Serial JTAG + UART0`

## 7. 如果要改需求，优先改哪里

### 7.1 改屏幕收到什么状态触发什么动画

看：

- `components/robot_link/robot_link.c`
- `components/emotion/emotion_engine.c`
- `components/display/display_service.c`

### 7.2 改触摸触发什么回传事件

看：

- `components/display/display_service.c`

关键事件：

- `EVT:TOUCH_SHY`
- `EVT:TOUCH_CENTER`

### 7.3 改第二个舵机动作幅度

看：

- `rdk/ir_wake_config.json`
- `rdk/ir_wake_bridge.py`

最常调的参数：

- `touch_servo_down_direction`
- `touch_servo_down_offset_us`
- `touch_servo_down_pulse_time`
- `touch_servo_up_offset_us`
- `touch_servo_up_pulse_time`
- `sensor_ignore_after_touch_servo_seconds`

### 7.4 改摄像头跟随行为

看：

- `rdk/tracker_config.json`
- `rdk/face_tracker.py`

## 8. 一句话定位主逻辑

如果只想快速找到“核心业务代码”，优先看这些文件：

- `main/app_main.c`
- `components/display/display_service.c`
- `components/robot_link/robot_link.c`
- `rdk/ir_wake_bridge.py`
- `rdk/face_tracker.py`
- `rdk/servo_control.py`

## 9. 推荐接手后的第一个动作

不要急着开发新功能，先做一次完整回归：

1. 屏幕串口单测
2. 红外桥接单测
3. 摄像头单测
4. 第二个舵机单测
5. 三者联调

联调步骤已经写在：

[JOINT_DEBUG.md](/Users/yirran/Downloads/rdx_camera_红外版/emoji_on_esp32s3_touch_lcd_4.3B-main/docs/JOINT_DEBUG.md)

## 10. 这份文档与 SESSION_HANDOFF 的关系

- `HANDOFF.md`
  给下一位工程师做第一入口
- `SESSION_HANDOFF.md`
  保存历史开发过程和阶段性上下文

如果你只想快速上手，先看 `HANDOFF.md`。  
如果你想追溯“为什么当时这样做”，再看 `SESSION_HANDOFF.md`。
