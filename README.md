# 罗技 G304 电量查看器

Windows 上读取罗技 G304 Lightspeed 无线鼠标电量的小工具。
**纯 ctypes 调系统 DLL，零第三方依赖**——不用 hidapi、pystray、Pillow、pywin32，
装了 Python 就能跑。

## 支持的设备

| 设备 | 接收器 VID:PID | 读取方式 |
|---|---|---|
| 罗技 G304 Lightspeed | `046D:C53F` | 罗技 HID++ 2.0，特性 `0x1000`（Battery Status） |

实测环境：G304 + LIGHTSPEED 接收器，设备索引 `1`，20 字节 HID++ 长报文。
其他罗技鼠标如果实现了同一组 HID++ 特性，也可能直接可用，但本项目只对 G304 做过验证。

## 使用方法

```bash
python my_devices.py            # 简洁面板（默认自动刷新）
python my_devices.py --once     # 命令行读一次就退出
```

界面功能：

- 大号电量百分比 + 进度条，颜色随电量变化（≥60 绿 / 30–59 黄 / <30 红）
- 自动刷新，间隔可选 30 秒 ~ 30 分钟
- 一键启动/退出托盘程序
- 开机自启（写入「启动」文件夹的快捷方式，任务管理器里可见、可禁用）
- 重建桌面链接（指向项目目录和启动脚本，不复制文件）

托盘程序：

```bash
pythonw tray_app.py             # 静默驻留
python  tray_app.py --debug     # 前台运行并输出过程信息
python  tray_app.py --status    # 查看配置 / 自启 / 运行状态
python  tray_app.py --quit      # 退出托盘
```

托盘里只有一个电量数字图标：右键打开面板，左键立即刷新，悬停显示电量与更新时间。

调试其他设备：

```bash
python app.py
```

完整调试版可以枚举所有 HID 接口、转储 Feature Report、探测 Report ID、试发命令。

## 读取原理

1. 枚举 HID 接口，找 VID `046D`、usage page `0xFF00` 的罗技厂商集合。
   优先选输出长度 20 字节的接口（HID++ 长报文），其次 7 字节短报文。
2. 发送 HID++ 2.0 长报文（Report ID `0x11`），设备索引从 `1` 到 `6` 逐个探测。
3. 用特性 `0x1000` 查特性索引，再调用函数 `0x00`：

   ```
   11 <index> <featureIndex> 11 00 ...
   ```

   回复第 4 字节是电量百分比，第 6 字节是状态（0=放电，1=充电，3=充满）。
4. 如果设备没有 `0x1000`，依次回退到 `0x1004`（统一电池）和 `0x1001`（电池电压）。
5. 设备名通过特性 `0x0005` 读取，例如 `G304 Lightspeed Wireless Gaming Mouse`。
6. 面板和托盘可能同时读取，用命名互斥量串行化，避免两边互相抢 HID++ 回复。

常见错误提示：

- 没找到接收器 → 接收器没插好
- 接收器在、鼠标没响应 → 鼠标休眠或没电，动一下鼠标再刷新

## 文件结构

| 文件 | 作用 |
|---|---|
| `g304.py` | G304 电量读取（HID++ 2.0） |
| `my_devices.py` | 简洁面板 + 命令行 |
| `tray_app.py` | 托盘常驻程序 |
| `tray_common.py` | 配置、开机自启、进程间通信 |
| `hid_win.py` | 纯 ctypes 的 HID 枚举与读写 |
| `win_icon.py` | 用 GDI 现画托盘数字图标 |
| `win_link.py` / `win_shell.py` | 桌面软连接、启动文件夹快捷方式 |
| `app.py` / `battery.py` | 完整调试版与通用协议探测 |

## 验证

```bash
python my_devices.py --once
# 罗技 G304: 90%   HID++ 2.0 特性 0x1000 · 设备索引 1
```

托盘连续启停、退出后无残留进程，均已实测。

## 适配其他设备

`PROMPT.md` 里保留了从零逆向一个 2.4G 外设电量协议的完整 prompt、踩坑清单和验收标准，
可以交给一个能执行命令、读写文件的 Agent 照做。
