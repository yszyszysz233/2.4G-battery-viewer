# -*- coding: utf-8 -*-
"""darkrim_proto.py —— DARKRIM（杰理 Jieli 方案）2.4G 耳机 dongle 的电量读取

协议是靠逆向厂商软件（DARKRIM 0.1.26，Qt + hidapi）然后抓包 + 爆破 CRC 得到的，
已在真机 DARKRIM SHADOW 上验证通过。完整协议说明见本文件顶部注释。

传输：HID 厂商接口，UsagePage=0xFF02 Usage=0x0002，报告长度 65 字节（无 Report ID）。

帧格式（65 字节，第 0 字节固定 0x00）：
    偏移  说明
    0     0x00 固定
    1     0xFE 帧头
    2     序号（每次请求 +1）
    3     0x10 = 本机侧(dongle) / 0x11 = 耳机侧
    4     0x00
    5     0x02 / 0x03
    6     分片序号
    7     0x02 = 还有后续分片
    8-9   载荷长度（小端）
    10..  载荷（TLV：type(1) + len(1) + value(len)）
    ...   紧接载荷之后 2 字节 CRC16-CCITT-FALSE（大端）
          CRC 覆盖范围 = 帧[1 : 10+len]

TLV 类型（实测）：
    0x01  状态值（本工具按电量解读，实测 0x01 01 46 即 70）
    0x02  设备名（"DARKRIM SHADOW"）
    0x03  EQ 数据（多分片）
    0x04  dongle 固件版本
    0x05  耳机固件版本
    0x06  状态码（实测 04）
    0x09  基础信息
    0x0B  地址/ID（6 字节）
    0xFF  校验信息
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import hid_win as hw
from hid_win import HidDevice

VID = 0x3654
USAGE_PAGE = 0xFF02
USAGE = 0x0002
REPORT_LEN = 65

CH_LOCAL = 0x10
CH_HEADSET = 0x11

TLV_BATTERY = 0x01
TLV_NAME = 0x02
TLV_FW_DONGLE = 0x04
TLV_FW_HEADSET = 0x05

TLV_NAMES = {
    0x01: "状态/电量", 0x02: "设备名", 0x03: "EQ 数据",
    0x04: "dongle 固件版本", 0x05: "耳机固件版本", 0x06: "状态码",
    0x09: "基础信息", 0x0B: "地址/ID", 0xFF: "校验信息",
}


def crc16_ccitt(data: bytes) -> int:
    """CRC16-CCITT-FALSE：poly 0x1021，init 0xFFFF，不反转。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_frame(seq: int, payload: bytes, ch: int = CH_LOCAL, sub: int = 0x02,
                frag: int = 0, more: int = 0) -> bytes:
    f = bytearray(REPORT_LEN)
    f[0] = 0x00
    f[1] = 0xFE
    f[2] = seq & 0xFF
    f[3] = ch
    f[4] = 0x00
    f[5] = sub
    f[6] = frag
    f[7] = more
    f[8] = len(payload) & 0xFF
    f[9] = (len(payload) >> 8) & 0xFF
    f[10:10 + len(payload)] = payload
    crc = crc16_ccitt(bytes(f[1:10 + len(payload)]))
    f[10 + len(payload)] = (crc >> 8) & 0xFF
    f[11 + len(payload)] = crc & 0xFF
    return bytes(f)


@dataclass
class Frame:
    seq: int
    ch: int
    sub: int
    frag: int
    more: int
    payload: bytes
    crc_ok: bool
    raw: bytes

    def tlvs(self) -> list[tuple[int, bytes]]:
        """把载荷拆成 (type, value) 列表。"""
        out = []
        i = 0
        p = self.payload
        while i + 2 <= len(p):
            t, ln = p[i], p[i + 1]
            out.append((t, p[i + 2:i + 2 + ln]))
            i += 2 + ln
        return out


def parse_frame(report: bytes) -> Optional[Frame]:
    if len(report) < 12 or report[1] != 0xFE:
        return None
    ln = report[8] | (report[9] << 8)
    if 10 + ln + 2 > len(report):
        return None
    payload = report[10:10 + ln]
    crc = (report[10 + ln] << 8) | report[11 + ln]
    return Frame(seq=report[2], ch=report[3], sub=report[5], frag=report[6],
                 more=report[7], payload=payload,
                 crc_ok=crc == crc16_ccitt(report[1:10 + ln]), raw=report)


def find_interface() -> Optional[hw.HidInterface]:
    """找到 dongle 的命令接口（0xFF02:0x0002，65 字节）。"""
    for d in hw.enumerate_hid():
        if d.vid == VID and d.usage_page == USAGE_PAGE and d.output_len == REPORT_LEN:
            return d
    # 放宽条件
    for d in hw.enumerate_hid():
        if d.vid == VID and d.usage_page == USAGE_PAGE:
            return d
    return None


def query(dev: HidDevice, seq: int, tlv_type: int, ch: int = CH_LOCAL,
          timeout: float = 0.5, sub: int = 0x02) -> list[Frame]:
    """发一条 TLV 查询（len=0），把这一轮回复都收回来。"""
    if dev.caps.InputReportByteLength:
        dev.drain()
    dev.write_output(build_frame(seq, bytes([tlv_type, 0x00]), ch=ch, sub=sub))
    frames: list[Frame] = []
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        r = dev.read_input(timeout_ms=120)
        if not r:
            continue
        f = parse_frame(r)
        if f and f.crc_ok and f.seq == (seq & 0xFF):
            frames.append(f)
    return frames


@dataclass
class DarkrimInfo:
    online: bool = False
    battery: Optional[int] = None          # 0-100
    name: str = ""
    dongle_firmware: str = ""
    headset_firmware: str = ""
    address: str = ""
    status_code: Optional[int] = None
    battery_raw: Optional[int] = None
    raw_tlvs: list = field(default_factory=list)
    detail: str = ""


def _ascii_from_tlv(value: bytes) -> str:
    return "".join(chr(b) for b in value if 32 <= b < 127).strip()


def read_info(iface: Optional[hw.HidInterface] = None,
              timeout: float = 0.5) -> DarkrimInfo:
    """读取 dongle + 耳机的信息与电量。"""
    info = DarkrimInfo()
    iface = iface or find_interface()
    if iface is None:
        info.detail = "没找到 DARKRIM dongle（0xFF02 接口）"
        return info
    try:
        with HidDevice(iface.path, write=True) as dev:
            seq = 0

            def do(tlv: int, ch: int = CH_LOCAL) -> list[Frame]:
                nonlocal seq
                out = query(dev, seq, tlv, ch=ch, timeout=timeout)
                seq = (seq + 1) & 0xFF
                return out

            # 先问电量（TLV 0x01）
            for f in do(TLV_BATTERY):
                for t, v in f.tlvs():
                    if t == TLV_BATTERY and len(v) >= 1:
                        info.battery_raw = v[0]
                        info.battery = v[0] if v[0] <= 100 else None
                        info.online = True
                        info.raw_tlvs.append((t, v.hex(" ")))
            # 再问设备名、固件、地址、状态
            for tlv in (TLV_NAME, TLV_FW_DONGLE, TLV_FW_HEADSET, 0x0B, 0x06, 0x09):
                for f in do(tlv):
                    for t, v in f.tlvs():
                        if t == TLV_NAME:
                            info.name = _ascii_from_tlv(v)
                        elif t == TLV_FW_DONGLE:
                            info.dongle_firmware = _ascii_from_tlv(v)
                        elif t == TLV_FW_HEADSET:
                            info.headset_firmware = _ascii_from_tlv(v)
                        elif t == 0x0B:
                            info.address = "-".join(f"{b:02X}" for b in v)
                        elif t == 0x06 and len(v) >= 1:
                            info.status_code = v[0]
                        if t not in (0x03, 0x14):
                            info.raw_tlvs.append((t, v.hex(" ") if v else "-"))
            info.online = True
            info.detail = (f"设备={info.name or '?'} 固件(dongle/耳机)="
                           f"{info.dongle_firmware or '?'}/{info.headset_firmware or '?'} "
                           f"状态码={info.status_code}")
    except Exception as e:
        info.detail = f"读取失败：{e}"
    return info


def read_battery_only(iface: Optional[hw.HidInterface] = None,
                      timeout: float = 0.4) -> tuple[Optional[int], str]:
    """轻量读取：只问一次电量（TLV 0x01），约 0.4 秒，适合托盘常驻轮询。

    返回 (电量, 错误说明)。
    """
    iface = iface or find_interface()
    if iface is None:
        return None, "没找到 DARKRIM dongle"
    try:
        with HidDevice(iface.path, write=True) as dev:
            for f in query(dev, 0, TLV_BATTERY, ch=CH_LOCAL, timeout=timeout):
                for t, v in f.tlvs():
                    if t == TLV_BATTERY and v:
                        return (v[0] if v[0] <= 100 else None), ""
        return None, "耳机没有上报电量（确认耳机已开机并连上）"
    except Exception as e:
        return None, f"读取失败：{e}"


if __name__ == "__main__":
    r = read_info()
    print("在线:", r.online)
    print("电量:", r.battery, f"(原始 {r.battery_raw})")
    print("设备名:", r.name)
    print("固件:", r.dongle_firmware, "/", r.headset_firmware)
    print("地址:", r.address, " 状态码:", r.status_code)
    print("说明:", r.detail)
    print("所有 TLV:")
    for t, v in r.raw_tlvs:
        print(f"   type=0x{t:02X} ({TLV_NAMES.get(t, '?')})  {v}")
