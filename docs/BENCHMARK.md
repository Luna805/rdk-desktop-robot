# 版本对比测试

每个版本发布前，用同一套场景测一遍，结果写进 [CHANGELOG.md](../CHANGELOG.md)。
基线是黑客松版本 `v1.0.0`。

## 1. 在 RDK 上并排部署两个版本

在电脑上的仓库里导出两份 `rdk/` 目录：

```bash
git archive v1.0.0 rdk | tar -x -C /tmp && mv /tmp/rdk /tmp/face_tracker_v1.0.0
git archive HEAD rdk | tar -x -C /tmp && mv /tmp/rdk /tmp/face_tracker_v1.1.0
scp -r /tmp/face_tracker_v1.0.0 /tmp/face_tracker_v1.1.0 sunrise@<RDK_IP>:~/
```

`v1.0.0` 里没有 `tools/`，打分工具统一用新版本目录里的。

测试前停掉后台服务，避免抢摄像头、串口和舵机：

```bash
sudo systemctl stop rdk-face-tracker rdk-ir-wake || true
mkdir -p ~/bench_logs
```

## 2. 采集日志

所有命令都必须加 `python3 -u`，再经过 `tools/tslog.py` 加时间戳，否则打分工具读不到。
下面以 `v1.0.0` 为例，测 `v1.1.0` 时把两处 `v1.0.0` 换掉即可。

```bash
cd ~/face_tracker_v1.0.0
sudo python3 -u face_tracker.py 2>&1 | python3 ~/face_tracker_v1.1.0/tools/tslog.py | tee ~/bench_logs/v1.0.0_<场景>.log
```

红外 / 触摸场景换成：

```bash
sudo python3 -u ir_wake_bridge.py 2>&1 | python3 ~/face_tracker_v1.1.0/tools/tslog.py | tee ~/bench_logs/v1.0.0_<场景>.log
```

## 3. 测试场景

同一场景两个版本要用同样的人、位置、光线。每个场景跑完按 `Ctrl+C`。

| 场景 | 日志名 | 操作 | 关注指标 |
|---|---|---|---|
| A 静坐 | `static` | 正对机器人坐好，保持不动 60 秒 | `pulses_per_min`、`reversals`（理想都为 0） |
| B 阶跃 | `step` | 从正中慢慢移到画面左边缘停住，等它转回来对准；再到右边缘。左右各 5 次 | `settle_s_median`、`settle_pulses_median`、`reversals` |
| C 无人 | `idle_ir` | 跑红外桥接，机器人前面不放东西，10 分钟 | `ir_wakes`（理想为 0，即误唤醒次数） |
| D 触摸 | `touch` | 跑红外桥接，快速交替点屏幕"中上"和"正中"共 20 次，最后点一次"正中" | `touch_servo_ignored`；**目测**头最后是否回到初始角度 |
| E 拔线 | `unplug` | 跑红外桥接，运行中拔掉屏幕 Type-C，5 秒后插回，再遮一次红外 | `serial_recoveries`、`serial_recover_s_max`；屏幕是否还响应 |

预期差异：

- `v1.0.0` 在场景 E 拔线后进程直接退出（手动运行时不会自动恢复）。
- `v1.0.0` 在场景 D 可能出现头越抬越高。
- 场景 A/B 的差异来自滞回和丢弃旧帧，`v1.1.0` 的 `reversals` 应更少。

## 4. 出报告

```bash
cd ~/face_tracker_v1.1.0
python3 tools/bench_report.py ~/bench_logs/v1.0.0_step.log ~/bench_logs/v1.1.0_step.log
```

每个场景一张对比表。把关键数字和目测结论写进 `CHANGELOG.md` 对应版本下。

## 指标说明

| 指标 | 含义 |
|---|---|
| `fps_mean` / `fps_min` | 追踪程序每 5 秒打印一次的帧率 |
| `pulses_per_min` | 每分钟舵机脉冲数，静坐时越少越稳 |
| `reversals` | 1.5 秒内出现方向相反的两次脉冲且中间没对准过，即"转过头又转回来" |
| `settle_s_median` | 从开始修正到画面里显示对准（`[hold]`）的时间中位数 |
| `settle_pulses_median` | 每次修正用了几个脉冲 |
| `ir_wakes` | 红外触发唤醒次数 |
| `serial_recover_s_max` | 串口断开到重新连上的最长时间（仅 `v1.1.0` 起有） |
