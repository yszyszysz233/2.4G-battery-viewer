# -*- coding: utf-8 -*-
"""mchose_proto.py —— MCHOSE G87（2.4G 无线键盘）的电量读取

协议是逆向官方网页驱动（MCHOSE HUB → https://www.mchose.com.cn/#/connectDevice）
的 WebHID 报文得到的，已在真机 G87 上逐条验证。完整说明见项目里的
`MCHOSE-G87-协议.md`。

传输：HID 厂商接口，UsagePage=0xFF02 Usage=0x0002，报文 20 字节
（1 字节 Report ID + 19 字节数据），Report ID 固定 0x13，
不发 Feature Report，只有 Output 下发 / Input 上收。

19 字节数据布局（请求和响应同一套）：
    偏移  说明
    0     命令号 cmd
    1     包总数 total（请求填 0x01）
    2     包序号 index（请求填 0x00，响应从 0 递增）
    3     载荷长度 len
    4..   载荷 payload
    18    校验和 = (0x13 + 数据[0:18] 之和) & 0xFF

实测命令：
    0x01  心跳/空操作，响应不稳定
    0x05  设备状态块（10 字节，[4]=连接方式 1=有线 2=2.4G）
    0x0A  设备主动上报（不是查询应答！载荷 05 LL SS 00 即电量+充电状态）
    0x41  配置表 A（36 包 × 14 字节，未解析）
    0x44  配置表 B（10 包，疑为灯效，未解析）
    0x49  配置表 C（35 包 × 14 字节，疑为按键掩码，未解析）
    0x4A  电量：响应载荷 = [电量百分比, 充电状态]
    0x4B / 0x04 / 0x06  无应答
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import hid_win as hw
from hid_win import HidDevice

VID = 0x41E4
PID = 0x2001
USAGE_PAGE = 0xFF02
USAGE = 0x0002

REPORT_ID = 0x13
REPORT_LEN = 20
DATA_LEN = 19

CMD_ACK = 0x01
CMD_STATUS = 0x05
CMD_NOTIFY = 0x0A
CMD_TABLE_A = 0x41
CMD_TABLE_B = 0x44
CMD_TABLE_C = 0x49
CMD_BATTERY = 0x4A

# 充电状态字节（实测，见 MCHOSE-G87-协议.md §5.3）
CHARGE_CHARGING = 0x10        # 插着线，充电中
CHARGE_DISCHARGING = 0x01     # 拔线，放电中
CHARGE_TRANSITION = 0x00      # 刚拔线的过渡态

CHARGE_TEXT = {
    CHARGE_CHARGING: "充电中",
    CHARGE_DISCHARGING: "未充电",
    CHARGE_TRANSITION: "状态切换中",
}

# 连接方式（cmd 0x05 载荷第 4 字节，与官方驱动的 toConnectStatus 一致）
CONNECT_TEXT = {1: "有线", 2: "2.4G"}


def checksum(data: bytes) -> int:
    """校验和 = (Report ID + 数据[0:18] 之和) & 0xFF。"""
    return (REPORT_ID + sum(data[:DATA_LEN - 1])) & 0xFF


def build(cmd: int, payload: bytes = b"", total: int = 1, index: int = 0) -> bytes:
    """构造一个 20 字节的请求报文（含 Report ID）。"""
    if len(payload) > DATA_LEN - 4:
        raise ValueError("载荷过长")
    data = bytearray(DATA_LEN)
    data[0] = cmd
    data[1] = total & 0xFF
    data[2] = index & 0xFF
    data[3] = len(payload) & 0xFF
    data[4:4 + len(payload)] = payload
    data[DATA_LEN - 1] = checksum(data)
    return bytes([REPORT_ID]) + bytes(data)


@dataclass
class Frame:
    cmd: int
    total: int
    index: int
    length: int
    payload: bytes
    cks_ok: bool
    raw: bytes

    @property
    def level(self) -> Optional[int]:
        return self.payload[0] if len(self.payload) >= 1 else None

    @property
    def charge_state(self) -> Optional[int]:
        return self.payload[1] if len(self.payload) >= 2 else None


def parse(report: bytes) -> Optional[Frame]:
    """解析一条 Input Report（20 字节）。"""
    if len(report) < 4:
        return None
    rid, data = report[0], report[1:]
    if rid != REPORT_ID or len(data) < DATA_LEN:
        return None
    ln = min(data[3], DATA_LEN - 4)
    return Frame(cmd=data[0], total=data[1], index=data[2], length=ln,
                 payload=bytes(data[4:4 + ln]),
                 cks_ok=data[DATA_LEN - 1] == checksum(data), raw=bytes(report))


def find_interface() -> Optional[hw.HidInterface]:
    """找到键盘的命令接口（0xFF02:0x0002）。"""
    for d in hw.enumerate_hid():
        if d.vid == VID and d.usage_page == USAGE_PAGE:
            return d
    return None


def query(dev: HidDevice, cmd: int, timeout: float = 0.5,
          payload: bytes = b"") -> list[Frame]:
    """发一条命令，把这一轮属于该命令的回复都收回来（按 index 排好序）。

    注意设备会主动推 0x0A 通知，这里只在没收到目标命令时才记录，不参与拼包。
    """
    if dev.caps.InputReportByteLength:
        dev.drain()
    dev.write_output(build(cmd, payload))

    frames: list[Frame] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = dev.read_input(timeout_ms=120)
        if not r:
            continue
        f = parse(r)
        if f is None or f.cmd != cmd:
            continue
        frames.append(f)
        if f.total and len(frames) >= f.total:
            break
    frames.sort(key=lambda f: f.index)
    return frames


def join_payload(frames: list[Frame]) -> bytes:
    """把多包应答的载荷拼起来。"""
    return b"".join(f.payload for f in frames)


@dataclass
class MchoseInfo:
    online: bool = False
    battery: Optional[int] = None          # 0-100
    charge_state: Optional[int] = None     # 原始状态字节
    charge_text: str = ""
    connect_mode: Optional[int] = None
    notify: Optional[tuple] = None         # 期间收到的主动上报
    raw_status: bytes = b""
    detail: str = ""
    error: str = ""
    frames: list = field(default_factory=list)


def read_info(iface: Optional[hw.HidInterface] = None,
              timeout: float = 0.5) -> MchoseInfo:
    """读电量 + 充电状态 + 状态块。"""
    info = MchoseInfo()
    if iface is None:
        try:
            iface = find_interface()
        except Exception as e:
            info.error = f"枚举 HID 失败：{e}"
            return info
    if iface is None:
        info.error = "没找到键盘（0x41E4:0x2001 / 0xFF02 接口）"
        return info
    try:
        with HidDevice(iface.path, write=True) as dev:
            frames = query(dev, CMD_BATTERY, timeout=timeout)
            info.frames = frames
            if frames:
                f = frames[0]
                info.online = True
                raw = f.level
                if raw is not None and raw <= 100:
                    info.battery = raw
                info.charge_state = f.charge_state
                if info.charge_state is not None:
                    info.charge_text = CHARGE_TEXT.get(
                        info.charge_state, f"未知(0x{info.charge_state:02X})")
                if not f.cks_ok:
                    info.detail = "（校验和不符）"
            else:
                info.error = "键盘没有应答（请敲一下键盘唤醒）"
                return info

            # 顺带取状态块，确认连接方式
            st = query(dev, CMD_STATUS, timeout=timeout)
            if st:
                info.raw_status = join_payload(st)
                if len(info.raw_status) >= 5:
                    info.connect_mode = info.raw_status[4]
    except Exception as e:
        info.error = f"读取失败：{e}"
    return info


def read_battery_only(iface: Optional[hw.HidInterface] = None,
                      timeout: float = 0.4) -> tuple[Optional[int], str, Optional[int]]:
    """轻量读取：只问一次电量，约 0.1~0.5 秒，适合托盘常驻轮询。

    返回 (电量, 错误说明, 充电状态字节)。
    """
    iface = iface or find_interface()
    if iface is None:
        return None, "没找到键盘（0x41E4:0x2001）", None
    try:
        with HidDevice(iface.path, write=True) as dev:
            for f in query(dev, CMD_BATTERY, timeout=timeout):
                if f.level is not None and f.level <= 100:
                    return f.level, "", f.charge_state
        return None, "键盘没有应答（请敲一下键盘唤醒）", None
    except Exception as e:
        return None, f"读取失败：{e}", None


if __name__ == "__main__":
    r = read_info()
    if r.error:
        print("读取失败：", r.error)
    else:
        print("电量:", r.battery, "%")
        print("充电:", r.charge_text, f"(0x{r.charge_state:02X})" if r.charge_state is not None else "")
        print("连接:", CONNECT_TEXT.get(r.connect_mode, r.connect_mode))
        print("状态块:", r.raw_status.hex(" "))
        print("说明:", r.detail or "-")
