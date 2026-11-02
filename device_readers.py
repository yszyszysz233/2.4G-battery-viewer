# -*- coding: utf-8 -*-
"""简洁面板与托盘共用的设备读取入口，自动选择已连接的支持设备。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import battery as bt
import hid_win as hw

MOUSE_TITLE = "雷蛇 毒蝰 V2 Pro"
KEYBOARD_TITLE = "狼蛛 F87 Pro"
DEVICE_TITLES = (MOUSE_TITLE, KEYBOARD_TITLE)
LEGACY_DEVICE_TITLES = ("ATK 鼠标", "DARKRIM 耳机")


@dataclass
class Reading:
    title: str
    subtitle: str
    level: Optional[int] = None
    status: str = ""
    detail: str = ""
    error: str = ""
    extra: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and (self.level is not None or self.status == "充电中")

    @property
    def display_level(self) -> str:
        if self.level is not None:
            return f"{self.level}%"
        return "充电中" if self.ok else "--"


def _read(title: str, interfaces: list[hw.HidInterface], vid: int, pids: tuple,
          strategy) -> Reading:
    reading = Reading(title, "未连接")
    matches = [d for d in interfaces if d.vid == vid and d.pid in pids]
    if not matches:
        reading.error = "没找到设备，请连接对应的 2.4G 接收器"
        return reading
    for iface in matches:
        try:
            result = strategy(iface)
        except Exception as exc:
            reading.error = f"读取出错：{exc}"
            continue
        if result is None:
            continue
        reading.subtitle = f"{iface.vid_pid} · {'USB 有线' if iface.pid == 0x00A5 else '2.4G 无线'}"
        if result.ok:
            reading.level = result.level
            reading.status = result.status_text
            reading.detail = result.source
            reading.extra = result.extra.get("notice", "")
            if not reading.extra:
                reading.extra = " · ".join(
                    f"{label} {result.extra[key]}"
                    for key, label in (("dongle_fw", "dongle 固件"), ("headset_fw", "耳机固件"))
                    if result.extra.get(key))
            reading.error = ""
            return reading
        reading.error = result.detail or "读取失败"
    if not reading.error:
        reading.error = ("无法访问设备接口，请检查运行权限或驱动占用"
                         if any(d.error for d in matches) else "没有找到匹配的电量查询接口")
    return reading


def _read_atk_interface(iface: hw.HidInterface) -> Optional[bt.BatteryResult]:
    if iface.usage_page != 0xFF02 and not (iface.usage_page == 0xFF05 and iface.output_len == 64):
        return None
    result = bt.strategy_vendor_protocol(iface)
    return result if result is not None else bt.strategy_standard_battery_usage(iface)


def read_all() -> list[Reading]:
    """每轮只枚举一次，面板和托盘使用相同的读取与状态处理。"""
    try:
        interfaces = hw.enumerate_hid()
    except OSError as exc:
        return [Reading(title, "", error=f"设备枚举失败：{exc}") for title in DEVICE_TITLES]
    # 没有新型号时维持原 ATK / DARKRIM 组合；也允许两组设备混用。
    have_razer = any(d.vid == 0x1532 and d.pid in bt.RAZER_VIPER_PIDS for d in interfaces)
    have_aula = any((d.vid, d.pid) == (bt.AULA_VID, bt.AULA_PID) for d in interfaces)
    have_atk = any((d.vid, d.pid) == (0x373B, 0x1216) for d in interfaces)
    have_darkrim = any((d.vid, d.pid) == (0x3654, 0x6901) for d in interfaces)
    mouse = (_read(MOUSE_TITLE, interfaces, 0x1532, bt.RAZER_VIPER_PIDS, bt.strategy_razer_viper)
             if have_razer or (have_aula and not have_atk) else
             _read(LEGACY_DEVICE_TITLES[0], interfaces, 0x373B, (0x1216,), _read_atk_interface))
    secondary = (_read(KEYBOARD_TITLE, interfaces, bt.AULA_VID, (bt.AULA_PID,), bt.strategy_aula_f87)
                 if have_aula or (have_razer and not have_darkrim) else
                 _read(LEGACY_DEVICE_TITLES[1], interfaces, 0x3654, (0x6901,), bt.strategy_darkrim))
    return [mouse, secondary]
