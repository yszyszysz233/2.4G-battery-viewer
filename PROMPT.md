# 可复用 Prompt：Windows 2.4G 外设电量小工具

> 用法：把下面「=== 提示词开始 ===」到「=== 提示词结束 ===」之间的内容整段复制，
> 粘给一个**能执行命令、读写文件的 Agent**（WorkBuddy / CodeBuddy / Cursor / Claude Code 等），
> 即可从零复刻这个工具。**纯聊天窗口不够**——枚举 HID、抓包、爆破 CRC 都得真跑起来。
> 里面包含了已经验证过的协议细节和踩过的坑，照做基本不会返工。

=== 提示词开始 ===

你是一个熟悉 Windows 底层开发的工程师，擅长用 Python `ctypes` 直接调用系统 DLL。
请帮我做一个 **Windows 10/11 上的无线外设电量小工具**，要求如下。

## 一、硬性约束

1. **零第三方依赖**：只用 Python 标准库 + `ctypes`（GUI 用 `tkinter`）。
   不要用 hidapi、pysystray、Pillow、pywin32。
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
2. 简洁面板里两张卡片显示正确百分比，颜色随电量变化（≥40 绿 / 11–39 黄 / <11 红）。
3. 任务管理器 →「启动」标签里能看到「电量托盘」，可直接启用/禁用，
   且与界面里的「开机自启」勾选框状态一致。
4. 桌面上是链接（不是副本）：指向项目目录的目录链接 + 指向启动脚本的文件链接。
5. 项目目录里**不产生任何日志文件**；`--debug` 时才有控制台输出。
6. 连续启停托盘 3 次正常；退出后无残留进程、无残留托盘图标。
7. 空闲时 CPU 占用接近 0（实测 75 秒内 < 0.5 秒 CPU）。

=== 提示词结束 ===
