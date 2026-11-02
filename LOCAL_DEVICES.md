# 雷蛇毒蝰 V2 Pro / 狼蛛 F87 Pro 设备适配

验证日期：2026-11-02，Windows，Python 3.12.7（64 位）。仅使用标准库与 ctypes。

## 雷蛇 毒蝰 V2 Pro

- 无线接收器 `1532:00A6`，命令集合为 `mi_00`，Usage `0001:0002`，Feature Report 长度 91。
- 集合由 Windows 鼠标驱动保留，使用 `dwDesiredAccess=0` 打开；仍可发送和接收 Feature Report。
  `HidDevice.write_output()` 继续要求读写权限。
- 报文：Report ID `00` + 90 字节协议体；transaction ID `1F`，data size `02`，command class `07`。
  电量命令 `80`，充电状态命令 `84`。
- 使用包含 Report ID 的下标：请求 `[2]=1F, [6]=02, [7]=07, [8]=命令`，
  校验 `[89]=XOR(report[3:89])`。回复 `[1]=02` 表示成功，值位于 `[10]`。
  校验总长度、Report ID、transaction ID、命令、data size、XOR 与状态后才接受结果。
- 实测电量原始值 `A1`（161），`round(161 * 100 / 255) = 63%`；充电值 `00`，放电中。
- 接收器睡眠超时状态 `04` 显示唤醒提示，不当作 0%。
- 支持同型号有线 ID `1532:00A5` 的相同 Feature Report 协议；本机尚未验证有线连接。
  不将其它雷蛇型号自动当作毒蝰 V2 Pro。

协议参考：[OpenRazer](https://github.com/openrazer/openrazer)、
[HaloBattery 的 Razer 协议说明](https://github.com/HeyOkay/HaloBattery/blob/main/providers/razer.py)。

## 狼蛛 F87 Pro

- 接收器 `3554:FA09`，Usage `FF02:0002`，Input / Output Report 都是 20 字节。
- 查询：`13 4A 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 5D`。
- 本机回复：`13 4A 01 00 02 64 01 00 00 00 00 00 00 00 00 00 00 00 00 C5`。
- `[5]` 为电量（本机为 100），`[6]` 为状态（`01` 放电、`10` 充电）；
  最后一个字节必须等于 `sum(report[:19]) & 0xFF`。
- 拒绝请求回显、错误的长度 / 命令 / 载荷头 / 状态 / 校验以及超出 0–100 的电量。
- `10` 充电状态下固件可能固定报告 100；此时百分比留空，面板显示「充电中」，
  托盘显示蓝色 `+`，悬停可查看状态。该分支由模拟报文验证，尚未实际插线测试。
- 通过独立异步读句柄接收回复；最多查询两次，失败提示切换 2.4G 模式并按键唤醒。
- 键盘有线和蓝牙连接不属于本次适配范围，不套用 2.4G 协议。

协议参考：[AULA F87 Control 的 DongleLink.read_battery](https://github.com/RadianceADM/aula-f87-control/blob/main/aula_f87/device.py)。

## 运行与验证

```powershell
python my_devices.py --once
python my_devices.py
pythonw tray_app.py
python -B -m unittest discover -s tests -v
```

面板、托盘及调试版共用同一协议；本机面板和托盘每轮只枚举一次 HID。
面板和托盘自动选择已连接的型号，并保留原 ATK / DARKRIM 支持；
可将毒蝰与 DARKRIM 耳机或 ATK 与狼蛛键盘混用。原设备与混合组合的路由由模拟测试覆盖。
命名互斥锁按 VID:PID 串行化本工具的跨进程查询，避免同时刷新时取走另一方的回复。
工具只查询电量和充电状态，不发送 DPI、灯效、按键映射、固件或保存配置命令。

若在开发工具的受限沙箱中出现所有 HID 接口 `err=5`，应在正常 Windows Python 环境运行。
这与设备缺失不同，也不表示日常运行必须使用管理员权限。
