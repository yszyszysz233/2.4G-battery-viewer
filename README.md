# 设备电量查看器

Windows 上读取 2.4G 无线外设电量的小工具。仅使用 Python 标准库、tkinter 和 ctypes，无需安装第三方依赖。

## 当前版本

- **罗技 GPW2 已适配**：通过 `046D:C547` LIGHTSPEED 接收器，使用 HID++ `0x1004` 读取真实百分比和充电状态；本机实测为 `PRO X Wireless`、27%、放电中。
- **VGN S99 已适配**：通过 `320F:5088` 接收器及 S99 产品名识别，读取设备上报的百分比；本机实测为 51%、未充电。
- **迈从 X9 已适配**：通过 `3837:6045` 接收器，读取设备上报的百分比及充电状态；本机实测为 20%、放电中，无需依赖迈从 HUB 读取。
- **主页两张卡片均可切换**：从下拉列表或「设备与托盘」选择设备，也可以选择「不显示设备」留空。
- **列表以电脑当前检测结果为准**：只显示当前枚举到的实体 HID 设备，使用设备上报名称；不补入未连接的预置型号或历史设备。接收器和有线设备分别显示，点击「重新扫描」或按 F5 同步插拔变化。
- 同一设备切换到另一张卡片时会交换位置，避免重复读取。选择、自动刷新开关和间隔会保存在本机，重启后恢复。
- **每台设备独立控制托盘**：在「设备与托盘」中逐台勾选「托盘显示」，可同时显示三台或更多设备。切换主页卡片不影响托盘选择，设置会保存；勾选设备会自动启动后台。
- 托盘全部取消时保留一个应用入口。旧配置会保留原先两台托盘设备；首次使用则启用已连接且支持读取的设备。
- 未适配的硬件只显示「待适配」，不发送猜测的厂商命令。
- 原有 ATK / VXE `373B:1216` 和 DARKRIM SHADOW `3654:6901` 的读取方式保留，本机未复测。

新增三款设备均已验证 2.4G 接收器读取。实际充电/充满状态切换、物理拔插和长时间休眠尚需进一步实测；有线和蓝牙模式未纳入此次适配。
同型号设备当前按 VID/PID 归为一项；GPW2、S99 和 X9 检测到多个同型号接收器时会提示，避免误读。

## 使用

```powershell
python my_devices.py          # 打开主页
python my_devices.py --once   # 读取主页所选设备后退出
python my_devices.py --list   # 列出可选择设备
python logitech_probe.py --debug  # 单独检查 GPW2 原始报文
python vgn_s99.py --debug      # 单独检查 S99 原始报文
python mchose_x9.py --debug    # 单独检查 X9 原始报文
python tray_app.py --debug    # 前台测试托盘
python tray_app.py --quit     # 退出托盘
```

也可双击仓库内的「我的设备电量.bat」。主页的「设置」保留开机启动与创建桌面入口功能。
F5 可立即刷新。托盘右键打开主页，左键刷新。
主页仍保留两张重点设备卡片；托盘单独配置数量，不受这两个位置限制。
关闭主页后，已开启的托盘会继续运行；可在主页点击「退出后台托盘」。

配置文件 `tray_config.json` 仅存设备选择和使用偏好，已加入 Git 忽略。`selected_devices` 保存主页两张卡片，`tray_devices` 保存独立的托盘设备列表；历史名称只用于已选设备断开后的状态提示，不生成列表条目。默认不写日志、不联网。

## 开发与验证

- `device_service.py`：设备发现、选择和统一读取入口，未知设备仅展示。
- `vgn_s99.py`：S99 无线电量查询，严格匹配产品名和通信接口。
- `mchose_x9.py`：X9 无线电量查询，仅匹配已验证的型号及电量接口；休眠回复不显示占位电量。
- `hid_lock.py`：同一接收器的跨进程读取锁。
- `logitech_hidpp.py`：HID++ 特性查询、准确匹配回复、短/长集合收发与跨进程读取锁。
- `battery.py`：对接现有电量策略与 `BatteryResult`。
- `my_devices.py` / `tray_app.py`：共用设备服务，界面和托盘一致。
- [GPW2_ADAPTATION.md](GPW2_ADAPTATION.md) / [VGN_S99_ADAPTATION.md](VGN_S99_ADAPTATION.md) / [MCHOSE_X9_ADAPTATION.md](MCHOSE_X9_ADAPTATION.md)：真机协议记录与当前验证范围。

```powershell
python -m unittest discover -s tests -v
```

下面保留上游的逆向方法与历史提示词。其“两台设备”约定描述的是上游版本；当前主页支持自由切换。

## 用 prompt 做一份你自己的

这套东西的价值不在代码，在**方法**。下面这段 prompt 把我做这个工具的全过程和结论都写进去了，
交给一个**能动手的 Agent** 就能按你自己的设备重做一份。

> **为什么必须是 Agent，不能是聊天 AI？**
> 这不是「写点代码」的活：要枚举本机 HID 接口、抓真实报文、试发命令、爆破 CRC、
> 再到真机上验证——每一步都得**实际在这台电脑上跑起来**。
> 纯聊天窗口只能给你一段猜出来的代码；能执行命令、能读写文件的 Agent 才调得通。
> （WorkBuddy / CodeBuddy / Cursor / Claude Code 这类都属于 Agent。）

1. **复制**下面折叠块里的 prompt（或直接打开 [`PROMPT.md`](PROMPT.md)）
2. **贴给一个 Agent**：WorkBuddy / CodeBuddy / Cursor / Claude Code 这类都行（关键是要能执行命令、读写文件）
3. **换成你的设备**：把 prompt 里「要读的两台设备」那一段替换掉。
   不知道 VID/PID 就这样查：设备管理器 → 右键设备 → 属性 → 详细信息 → 硬件 Id，
   会看到形如 `HID\VID_XXXX&PID_YYYY` 的值
4. **让它跑通**：Agent 会给出一套初始实现。如果读不到电量，让它用完整版的
   「原始探测 + 手动标定」把真实协议摸出来，再把结果补回 prompt 的协议段

prompt 里已经写好了硬性约束、协议模板、7 条会踩的坑和验收标准，照做基本不会返工。

<details>
<summary><b>点开看完整 prompt（可直接复制）</b></summary>

你是一个熟悉 Windows 底层开发的工程师，擅长用 Python `ctypes` 直接调用系统 DLL。
请帮我做一个 **Windows 10/11 上的无线外设电量小工具**，要求如下。

## 一、硬性约束

1. **零第三方依赖**：只用 Python 标准库 + `ctypes`（GUI 用 `tkinter`）。
   不要用 hidapi、pystray、Pillow、pywin32。
2. **三种形态**：
   - 简洁面板：只显示我这两台设备的两张卡片（大号百分比 + 进度条 + 数据来源）。
   - 托盘常驻：通知区域两个图标，图标内容就是电量数字。
   - 完整调试版：列出所有 HID 设备/接口，能转储 Feature Report、探测 Report ID、
     试发命令、手动标定协议。
3. **托盘交互**：右键图标 → 直接打开/前置简洁面板（已开则不重复开）；左键 → 立刻刷新一次；
   悬停 tooltip 显示"设备名：电量（更新时间）"。
4. **低 CPU**：用 `SetTimer` 驱动刷新（默认 60 秒，可调 30 秒~30 分钟），
   空闲时不轮询、不 sleep 循环；读取放后台线程，界面不卡。
5. **开机自启**：做成「启动」文件夹里的快捷方式（`.lnk`），
   这样任务管理器 →「启动」标签里能直接看到、能启用/禁用。
   不要用注册表 Run（虽然也能被任务管理器识别，但在启动文件夹里更直观、更容易管理）。
   快捷方式目标是 `pythonw.exe` + 脚本路径，**不能弹黑窗**。
6. **桌面入口用软连接**：桌面放一个指向项目目录的目录链接 + 指向启动脚本的文件链接，
   不要复制脚本。目录优先真符号链接，没有管理员/开发者模式时降级用 junction（`mklink /J`），
   文件降级用硬链接（`os.link`）。
7. **不写任何日志文件**：只保留 `--debug` 参数时的控制台输出。
8. 关闭托盘时进程必须真正退出，**不能留僵尸进程**（详见"已知的坑"）。

## 二、要读的两台设备

| 设备 | VID:PID | 说明 |
|---|---|---|
| ATK / VXE 无线鼠标 | `373B:1216` | 2.4G NANO 接收器 |
| DARKRIM SHADOW 耳机 | `3654:6901` | 杰理（Jieli）方案 dongle |

## 三、已验证的协议（直接照抄，不用再逆向）

### 1) ATK / VXE 鼠标（Nordic 方案）

- 接口：usage page `0xFF05`，输入 64 字节 / 输出 64 字节，**没有 Feature Report**。
- Report ID：`0x08`（即报文首字节）。
- 请求（64 字节，后面补 0）：

  ```
  08 7D 72 02 00 01 07 01 00 ... 00
  ```

- 回复校验：`resp[1] == 0x72 and resp[5] == 0x07`，**电量 = resp[7]**。
- 收不到回复通常是因为鼠标在休眠，提示用户"动一下鼠标"。

### 2) DARKRIM 耳机（杰理 JL_RCSP 私有协议）

- 接口：VID `0x3654`，65 字节报文，**无 Report ID**（首字节就是 `0x00`）。
- 帧格式：

  ```
  00 FE <seq> <10|11> 00 <02|03> <分片> <more> <len_LE16> <TLV 载荷> <CRC_BE16> 00...
  ```

- **CRC = CRC16-CCITT-FALSE（poly 0x1021, init 0xFFFF），
  覆盖 `帧[1 : 10+len]`，大端存放在 `帧[10+len]`、`帧[11+len]`。**
- 查询 = 把 TLV 长度置 0 发出去。TLV 类型：
  `0x01`=状态/电量、`0x02`=设备名、`0x03`=EQ、`0x04`=dongle 固件、
  `0x05`=耳机固件、`0x06`=状态码、`0x0B`=地址。
- 读电量：发 `TLV 0x01`（长度 0），回复里 TLV `0x01` 的第一个字节就是电量百分比。
- 可选信息：TLV `0x02` 给设备名（"DARKRIM SHADOW"）、`0x04`/`0x05` 给固件版本字符串。
- 耳机没开机 / 没连上时，dongle 不回复任何数据 —— 要给出明确提示。

### 3) 兜底（换设备时用）

优先级：标准 HID 电量字段（Generic Device Controls `0x0006/0x0020` Battery Strength）→
厂商协议表（Endgame `0x3367`、VAXEE `0x3057`、Nordic `0x1915`）→
罗技 HID++ 2.0（特性 `0x1000/0x1004/0x1001`）→ 监听 Input Report →
手动标定（把 Report ID / 电量字节下标 / 倍率存成 JSON）。

## 四、已知的坑（务必规避）

1. **绝对不要在同一 HID 句柄上"阻塞 ReadFile + 并发 WriteFile/IOCTL"** ——
   实测会直接杀掉进程（连 Python 异常栈都没有）。
   正确做法：用独立的、带 `FILE_FLAG_OVERLAPPED` 的读句柄 + 事件超时 + `CancelIoEx`。
2. **系统独占的顶层集合**（鼠标/键盘/笔/触摸屏）只能用 `dwDesiredAccess=0` 打开，
   能读属性和 Feature Report，但不能发指令。
3. **销毁托盘窗口后必须 `PostQuitMessage(0)`**（在 `WM_DESTROY` 里发）。
   只调 `DestroyWindow` 会留下"窗口没了、图标也没了、但进程还活着"的僵尸进程，
   它还占着单实例锁 → 之后再也启动不起来，而且 `pythonw` 没有控制台，用户看不到任何报错。
4. **Tkinter 不是线程安全的**：后台线程不要直接调 `root.after()`，
   用 `queue.Queue` 把结果交给主线程，主线程用 `after(100, pump)` 取。
5. **托盘图标不要"删了再加"**：`NIM_DELETE` 删掉的图标，用 `NIM_MODIFY` 是改不回来的，
   必须重新 `NIM_ADD`。另外不做 DPI 声明的话图标会被系统缩放得发糊（要 `SetProcessDPIAware`）。
6. **创建 .lnk 用纯 ctypes**：`CoCreateInstance(CLSID_ShellLink)` → `IShellLinkW` →
   `QueryInterface(IPersistFile)` → `Save()`。vtable 下标：
   `SetPath=20 SetArguments=11 SetWorkingDirectory=9 SetDescription=7 SetIconLocation=17`、
   `GetPath=3 GetArguments=10 GetWorkingDirectory=8`；`Load=5 Save=6`。
   GUID 字符串要用 `split("-", 3)` 解析（最后一节里还有连字符）。
7. **别让常驻程序"自动纠正"用户设置**：托盘只应当**读**开机自启的真实状态，
   绝不能因为读到一次不一致就替用户删/建启动项。

## 五、验收标准

1. `python my_devices.py --once` 能打印两行电量（鼠标 xx%、耳机 xx%）。
2. 简洁面板里两张卡片显示正确百分比，颜色随电量变化（≥60 绿 / 30–59 黄 / <30 红）。
3. 任务管理器 →「启动」标签里能看到「电量托盘」，可直接启用/禁用，
   且与界面里的「开机自启」勾选框状态一致。
4. 桌面上是链接（不是副本）：指向项目目录的目录链接 + 指向启动脚本的文件链接。
5. 项目目录里**不产生任何日志文件**；`--debug` 时才有控制台输出。
6. 连续启停托盘 3 次正常；退出后无残留进程、无残留托盘图标。
7. 空闲时 CPU 占用接近 0（实测 75 秒内 < 0.5 秒 CPU）。

</details>

## 环境要求

- Windows 10 / 11
- Python 3.10+（需要 `tkinter`，官方安装包默认自带）

## 目录说明

| 文件 | 说明 |
|---|---|
| `hid_win.py` | HID 底层：枚举接口、Feature Report 读写、异步 Input Report、字段解析 |
| `battery.py` | 多策略电量读取 + 厂商协议表 + 手动标定 |
| `darkrim_proto.py` | DARKRIM 杰理方案私有协议（帧构造 / CRC / TLV 解析） |
| `bt_battery.py` | 蓝牙耳机 HFP 电量（未公开 PnP 属性） |
| `my_devices.py` | **主界面**（简洁面板） |
| `tray_app.py` | 托盘程序（`Shell_NotifyIconW` + `SetTimer`，空闲几乎不占 CPU） |
| `tray_common.py` | 配置、开机自启、进程间消息 |
| `win_icon.py` | 点阵数字 → ICO → HICON（图标就是电量数字） |
| `win_link.py` | 软连接：符号链接 / 目录 junction / 硬链接，按权限自动降级 |
| `win_shell.py` | 已知文件夹路径 + 用 ctypes COM 创建 `.lnk` |
| `app.py` | 完整调试版界面 |
| `PROMPT.md` | 把整个项目整理成一份可复用的提示词 |

## 两个值得一提的实现细节

1. **同一 HID 句柄上并发「阻塞读 + 写」会直接杀掉进程**（连异常栈都没有）。
   正确做法是独立的 `FILE_FLAG_OVERLAPPED` 读句柄 + 事件超时 + `CancelIoEx`。
2. **销毁托盘窗口后必须 `PostQuitMessage(0)`**。只调 `DestroyWindow` 会留下
   「窗口和图标都没了、但进程还活着」的僵尸进程，它占着单实例锁，导致之后再启动都没反应。

更多坑见 [PROMPT.md](PROMPT.md)。

## 隐私

- **不联网**：没有任何 http / socket / urllib / 遥测调用，所有读取都在本地 HID 层完成。
- **不写日志**：默认不产生任何日志文件；只有 `--debug` 时才往控制台打印。
- **不上传**：设备序列号、地址等信息只在内存里用，不落盘。

## 说明

私有协议部分是通过逆向厂商软件 + 抓包分析得到的，仅供学习与个人使用。
