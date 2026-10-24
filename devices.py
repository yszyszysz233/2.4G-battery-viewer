# -*- coding: utf-8 -*-
"""devices.py —— 面板（my_devices.py）和托盘（tray_app.py）共用的读取层

只管三类设备：
    小米 2.4G 鼠标   VID 0x2717  -> xiaomi_proto.py
    雷蛇无线耳机     VID 0x1532  -> razer_proto.py
    赛睿无线耳机     VID 0x1038  -> steelseries_proto.py

每个 read_xxx() 都不会抛异常，返回一个 Reading；读不到时 Reading.error 写清楚原因
（没插接收器 / 耳机关机 / 型号不支持 / 被别的软件占用……）。
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Callable, Optional

import razer_proto
import steelseries_proto
import xiaomi_proto
from hid_common import DeviceBattery


@dataclass
class Reading:
    title: str
    subtitle: str
    level: Optional[int] = None
    status: str = ""
    detail: str = ""
    error: str = ""
    extra: str = ""
    approx: bool = False
    charging: Optional[bool] = None
    found: bool = True          # False = 没插接收器（托盘据此隐藏图标）

    @property
    def ok(self) -> bool:
        return self.level is not None

    @property
    def level_text(self) -> str:
        if self.level is None:
            return "--"
        return f"≈{self.level}%" if self.approx else f"{self.level}%"


# key, 标题, 读取函数, 没插接收器时的副标题
FAMILIES: list[tuple[str, str, Callable, str]] = [
    ("xiaomi", "小米鼠标", xiaomi_proto.read_battery, "无接收器"),
    ("razer", "雷蛇耳机", razer_proto.read_battery, "无接收器"),
    ("steelseries", "赛睿耳机", steelseries_proto.read_battery, "无接收器"),
]
TITLES = [f[1] for f in FAMILIES]


def to_reading(title: str, res: DeviceBattery, empty_sub: str = "无接收器") -> Reading:
    r = Reading(title, empty_sub)
    if not res.found:
        r.found = False
        r.error = res.detail or "没找到接收器（请把 2.4G 接收器插上）"
        return r
    r.subtitle = f"{res.vid:04X}:{res.pid:04X} · {res.model}" if res.pid else res.model
    r.charging = res.charging
    if res.charging is True:
        r.status = "充电中"
    elif res.charging is False:
        r.status = "未充电"
    bits = []
    if res.voltage_mv:
        bits.append(f"电压 {res.voltage_mv} mV")
    if res.approx and res.level is not None:
        bits.append("该型号只上报几档电量，数值为近似")
    r.extra = " · ".join(bits)
    if res.ok:
        r.level = res.level
        r.approx = res.approx
        r.detail = res.detail if res.detail not in ("", "充电中") else ""
    else:
        r.error = res.detail or "读取失败"
    return r


def _safe(title: str, fn: Callable, empty_sub: str, ifaces) -> Reading:
    try:
        return to_reading(title, fn(ifaces), empty_sub)
    except Exception as e:
        traceback.print_exc()
        return Reading(title, "", error=f"读取出错：{e}")


def _enumerate():
    import hid_win as hw
    return hw.enumerate_hid()


def read_xiaomi(ifaces=None) -> Reading:
    return _safe("小米鼠标", xiaomi_proto.read_battery, "无接收器", ifaces)


def read_razer(ifaces=None) -> Reading:
    return _safe("雷蛇耳机", razer_proto.read_battery, "无接收器", ifaces)


def read_steelseries(ifaces=None) -> Reading:
    return _safe("赛睿耳机", steelseries_proto.read_battery, "无接收器", ifaces)


def read_all() -> list[Reading]:
    """三类设备各读一次（只枚举一次 HID）。顺序：小米、雷蛇、赛睿。"""
    try:
        ifaces = _enumerate()
    except Exception as e:
        traceback.print_exc()
        return [Reading(t, "", error=f"枚举 HID 设备失败：{e}") for t in TITLES]
    return [read_xiaomi(ifaces), read_razer(ifaces), read_steelseries(ifaces)]


def read_all_dict() -> dict[str, Reading]:
    return dict(zip([f[0] for f in FAMILIES], read_all()))
