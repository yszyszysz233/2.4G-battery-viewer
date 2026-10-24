# -*- coding: utf-8 -*-
"""xiaomi_proto.py —— 小米（Xiaomi, VID 0x2717）2.4G 无线鼠标电量读取

协议来源（真机 + 官方 DLL 反汇编，不是猜的）：
  [DLL]  XiaoMiMouseLibrary.dll（随「小米键鼠」/ Xiaomi G-HUB 安装，PE32）
         消息 cmd 0x47 → 辅助函数约 0x100044b0
         Feature 请求 opcode 字 0x8108（LE 写到 [2]=08 [3]=81），[4]=0x01
         product_type==2 时 byte[5]=0x02（Mouse X1 / 50A5·50A6 实测）
         校验：sum(bytes[0..62]) → [63]=lo [64]=hi（DLL 约 0x10002030）
         成功：回复 [1]==0x01 且 [2:4] 回显 08 81；电量在 [7]（0..100）
         （旧实现误用 cmd 0x6b / opcode 0x8103，把 [9] 当电量，读到的是 flag=1）
  [LIVE] 本仓库在 Windows 真机 2717:50A5（产品串 XiaoMi Mouse X1）
         MI_02 / UsagePage 0xFFFF Usage 0x0001 / FeatureReportByteLength=65
         与官方软件同屏：官方约 55% 时，0x8108 连续读 [7]=55

公开资料仍然没有完整协议文档（libratbag #1800、hid-xiaomi.c 都不覆盖电量），
所以未知型号不会瞎编其它 opcode；只对匹配的 65 字节 vendor Feature 口发 0x8108。

仍保留两条有依据的兜底：
  1. 标准 HID 电量字段（Usage 0x06/0x20、0x85/0x01|0x66|0x65）——不用 0x06/0x29 信号强度
  2. 用户标定协议 protocols_user.json（app.py「手动标定」）
"""

from __future__ import annotations

import time
from typing import Optional

from hid_common import DeviceBattery, interface_number

VID = 0x2717

# Vendor Feature 通道：MI_02 / FFFF:0001 / 65 字节（X1 接收器实测）
VENDOR_USAGE_PAGE = 0xFFFF
VENDOR_USAGE = 0x0001
VENDOR_FEATURE_LEN = 65
VENDOR_OPCODE = 0x8108          # LE 落在请求 [2]=0x08 [3]=0x81
VENDOR_OPCODE_LO = VENDOR_OPCODE & 0xFF          # 0x08
VENDOR_OPCODE_HI = (VENDOR_OPCODE >> 8) & 0xFF   # 0x81
VENDOR_LEN_BYTE = 0x01          # DLL mov byte ptr [ebp-0x44], 1
VENDOR_SETTLE_S = 0.08
# G-HUB 会并发写 Feature 缓冲（8704/8404…）；多读几次直到回显本 opcode；
# 污染时重新 SetFeature，避免粘滞帧占满整轮重试。
VENDOR_GET_ATTEMPTS = 12
VENDOR_GET_GAP_S = 0.05
VENDOR_RESET_ON_POLLUTION = True

KNOWN_MODELS = {
    0x5091: "小米 USB 接收器（Mi USB Receiver）",
    0x5094: "小米 USB 接收器（Mi USB Receiver）",
    0x500B: "小米游戏鼠标接收器",
    0x5013: "小米无线鼠标",
    0x5017: "小米无线鼠标",
    0x50A5: "小米鼠标 X1",
    0x50A6: "小米鼠标 X1",
}

# DLL Open 解析出的 product_type；X1（50A5/50A6）实测必须为 2 才返回 status=01
PRODUCT_TYPE = {
    0x50A5: 2,
    0x50A6: 2,
}

# (UsagePage, Usage, 说明) —— 只要真正的电量字段
BATTERY_USAGES = [
    (0x0006, 0x0020, "Battery Strength"),
    (0x0085, 0x0001, "Absolute State of Charge"),
    (0x0085, 0x0066, "Relative State of Charge"),
    (0x0085, 0x0065, "Battery Level"),
]

SYSTEM_RESERVED = {(0x0001, 0x0001), (0x0001, 0x0002), (0x0001, 0x0006), (0x0001, 0x0007)}

UNSUPPORTED_HINT = ("该型号未公开电量协议，接收器也没有声明标准 HID 电量字段。"
                    "可以用 app.py 抓包后“手动标定”，之后就能读。")


def model_name(pid: int) -> str:
    return KNOWN_MODELS.get(pid, f"小米设备 2717:{pid:04X}")


def scale(value: int, logical_max: int) -> int:
    if logical_max and logical_max > 0 and logical_max != 100:
        return max(0, min(100, round(value * 100 / logical_max)))
    return max(0, min(100, value))


def match_usage(cap) -> Optional[str]:
    key = (cap.UsagePage, cap.u.NotRange.Usage)
    for page, usage, name in BATTERY_USAGES:
        if key == (page, usage):
            return name
    return None


# ---------------------------------------------------------------------------
# 官方 DLL 反汇编得到的 0x8108 Feature 协议（纯函数，方便单测）
# ---------------------------------------------------------------------------
def checksum65(buf: bytearray) -> None:
    """原地写入 [63]=sum(lo) [64]=sum(hi)；与 XiaoMiMouseLibrary.dll 一致。"""
    s = sum(buf[0:63]) & 0xFFFF
    buf[63] = s & 0xFF
    buf[64] = (s >> 8) & 0xFF


def build_battery_request(product_type: int = 2, param: int = 0) -> bytes:
    """构造 65 字节 GetBatteryLevel Feature 请求（cmd 0x47 / opcode 0x8108）。

    布局（与 DLL 0x100044b0 / 真机确认一致）：
      [0]=00 report id  [1]=00
      [2]=08 [3]=81     ← opcode 0x8108 小端字
      [4]=01            ← 长度字节（DLL 写 1）
      [5]=product_type  [6]=00  [7]=param  [8]=00
      [9..62]=00        [63..64]=checksum
    """
    b = bytearray(VENDOR_FEATURE_LEN)
    b[2] = VENDOR_OPCODE_LO
    b[3] = VENDOR_OPCODE_HI
    b[4] = VENDOR_LEN_BYTE
    b[5] = product_type & 0xFF
    b[7] = param & 0xFF
    checksum65(b)
    return bytes(b)


def parse_battery_response(resp: bytes) -> Optional[int]:
    """成功返回 0..100；status!=0x01、非本 opcode 回显或长度不够返回 None。

    电量在 [7]（DLL 把 out[6]=resp[7] 写入 msg+0xC；真机与官方 UI 同屏验证）。
    """
    if not resp or len(resp) < 8:
        return None
    if resp[1] != 0x01:
        return None
    # 要求回显 0x8108，避免把 G-HUB 的 8704/8404 粘滞帧当成电量
    if resp[2] != VENDOR_OPCODE_LO or resp[3] != VENDOR_OPCODE_HI:
        return None
    return max(0, min(100, int(resp[7])))


def product_types_for(pid: int) -> list[int]:
    """先试该 PID 已知的 product_type，再兜底 2/0/1（去重保序）。"""
    ordered = []
    if pid in PRODUCT_TYPE:
        ordered.append(PRODUCT_TYPE[pid])
    for t in (2, 0, 1):
        if t not in ordered:
            ordered.append(t)
    return ordered


def _is_vendor_iface(iface) -> bool:
    if (iface.feature_len or 0) < VENDOR_FEATURE_LEN:
        return False
    if iface.usage_page == VENDOR_USAGE_PAGE and iface.usage == VENDOR_USAGE:
        return True
    # 路径兜底：MI_02（X1 接收器电量口）
    return interface_number(getattr(iface, "path", "") or "") == 2


def _vendor_battery(iface, diag: list, product_types: list[int]) -> Optional[DeviceBattery]:
    """在 MI_02 65 字节 Feature 口上发 0x8108，读电量。"""
    if not _is_vendor_iface(iface):
        return None
    from hid_win import HidDevice

    last_status = None
    with HidDevice(iface.path, write=True) as dev:
        for pt in product_types:
            req = build_battery_request(product_type=pt, param=0)
            try:
                dev.set_feature_report(req)
            except OSError as e:
                diag.append(f"  vendor SET pt={pt} 失败：{e}")
                continue
            time.sleep(VENDOR_SETTLE_S)
            level = None
            level_byte = None
            for attempt in range(VENDOR_GET_ATTEMPTS):
                try:
                    resp = dev.get_feature_report(0x00, VENDOR_FEATURE_LEN)
                except OSError as e:
                    diag.append(f"  vendor GET pt={pt} 失败：{e}")
                    break
                if not resp:
                    continue
                last_status = resp[1] if len(resp) > 1 else None
                level_byte = resp[7] if len(resp) > 7 else None
                level = parse_battery_response(resp)
                if level is not None:
                    break
                # 污染帧（status 看似 ok 但 opcode 不是 08 81）→ 重新下发请求
                polluted = (
                    VENDOR_RESET_ON_POLLUTION
                    and len(resp) >= 4
                    and (resp[2] != VENDOR_OPCODE_LO or resp[3] != VENDOR_OPCODE_HI)
                )
                if polluted:
                    try:
                        dev.set_feature_report(req)
                    except OSError:
                        pass
                    time.sleep(VENDOR_SETTLE_S)
                elif attempt + 1 < VENDOR_GET_ATTEMPTS and VENDOR_GET_GAP_S:
                    time.sleep(VENDOR_GET_GAP_S)
            diag.append(
                f"  vendor 0x8108 pt={pt} status=0x{(last_status or 0):02X} "
                f"level_byte={level_byte if level_byte is not None else '-'} "
                f"({'ok' if level is not None else 'nak'})"
            )
            if level is not None:
                return DeviceBattery(
                    found=True, online=True, level=level, charging=None,
                    detail=f"小米官方协议 0x8108（product_type={pt}）",
                )
    if last_status == 0x03:
        diag.append("  vendor 口返回 sticky NAK(0x03)，鼠标可能离线或 product_type 不对")
    return None


def _feature_battery(iface, diag: list) -> Optional[DeviceBattery]:
    if not iface.feature_len:
        return None
    from hid_win import HidDevice, HidP_Feature
    with HidDevice(iface.path, write=False) as dev:
        for cap in dev.value_caps(HidP_Feature):
            name = match_usage(cap)
            if not name:
                continue
            report = dev.get_feature_report(cap.ReportID)
            value = dev.usage_value(HidP_Feature, report, cap)
            diag.append(f"  Feature {name} id=0x{cap.ReportID:02X} 值={value} 上限={cap.LogicalMax}")
            if value is None:
                continue
            return DeviceBattery(found=True, online=True, level=scale(value, cap.LogicalMax),
                                 detail=f"标准 HID 电量字段（{name}）")
    return None


def _input_battery(iface, diag: list, seconds: float) -> Optional[DeviceBattery]:
    if not iface.input_len or (iface.usage_page, iface.usage) in SYSTEM_RESERVED:
        return None
    from hid_win import HidDevice, HidP_Input
    with HidDevice(iface.path, write=False) as dev:
        caps = [(c, match_usage(c)) for c in dev.value_caps(HidP_Input)]
        caps = [(c, n) for c, n in caps if n]
        if not caps:
            return None
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            report = dev.read_input(timeout_ms=200)
            if not report:
                continue
            for cap, name in caps:
                if report[0] != cap.ReportID and cap.ReportID:
                    continue
                value = dev.usage_value(HidP_Input, report, cap)
                if value is None:
                    continue
                diag.append(f"  Input {name} id=0x{cap.ReportID:02X} 值={value}")
                return DeviceBattery(found=True, online=True, level=scale(value, cap.LogicalMax),
                                     detail=f"标准 HID 电量字段（{name}，输入报告）")
        diag.append(f"  {iface.describe_usage()} 声明了电量字段，但 {seconds:.1f}s 内没有上报"
                    "（鼠标可能在休眠，动一下再试）")
    return None


def _user_protocol(iface, diag: list) -> Optional[DeviceBattery]:
    try:
        import battery as bt
    except Exception:            # pragma: no cover
        return None
    if not any(s.vid == VID for s in bt.load_user_protocols()):
        return None
    res = bt.strategy_vendor_protocol(iface)
    if res is None:
        return None
    diag.append(f"  用户标定协议：{res.source} {res.detail}")
    if res.ok and res.level is not None:
        chg = True if res.status in ("charging",) else (None if res.status == "unknown" else False)
        return DeviceBattery(found=True, online=True, level=res.level, charging=chg,
                             detail=f"用户标定协议（{res.source}）")
    return None


def read_battery(all_ifaces: Optional[list] = None, listen_seconds: float = 1.5) -> DeviceBattery:
    if all_ifaces is None:
        from hid_win import enumerate_hid
        all_ifaces = enumerate_hid()
    mine = [i for i in all_ifaces if i.vid == VID]
    if not mine:
        return DeviceBattery(found=False, vid=VID, detail="没找到小米 2.4G 接收器（VID 2717）")
    pid = sorted({i.pid for i in mine}, key=lambda p: (p not in KNOWN_MODELS, p))[0]
    diag: list = [f"[Xiaomi] {', '.join(sorted({i.vid_pid for i in mine}))}"]
    errors = []
    pts = product_types_for(pid)

    # 1) 官方 0x8108（优先，X1 真机 + 官方 UI 同屏验证）
    for iface in mine:
        try:
            res = _vendor_battery(iface, diag, pts)
        except OSError as e:
            errors.append(str(e))
            continue
        if res is not None:
            res.vid, res.pid = iface.vid, iface.pid
            res.model = model_name(iface.pid)
            res.diag = diag
            return res

    # 2) 标准 HID / 用户标定 / Input 监听
    for step in (_feature_battery, _user_protocol,
                 lambda i, d: _input_battery(i, d, listen_seconds)):
        for iface in mine:
            try:
                res = step(iface, diag)
            except OSError as e:
                errors.append(str(e))
                continue
            if res is not None:
                res.vid, res.pid = iface.vid, iface.pid
                res.model = model_name(iface.pid)
                res.diag = diag
                return res

    detail = UNSUPPORTED_HINT
    if errors:
        detail += f"（部分接口打开失败：{errors[0]}）"
    return DeviceBattery(found=True, vid=VID, pid=pid, model=model_name(pid),
                         detail=detail, diag=diag)


if __name__ == "__main__":
    r = read_battery()
    print(r.model, r.level, r.detail)
    print("\n".join(r.diag or []))
