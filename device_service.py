"""Device discovery and readings shared by all application entry points.

Unknown hardware is listed for selection but never sent guessed commands.
Selection keys identify a VID/PID model, independent of HID enumeration order.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import battery as bt
import darkrim_proto as drp
import hid_win as hw


@dataclass
class Device:
    key: str
    title: str
    provider: str = "unsupported"
    connection: str = "USB / 2.4G"
    interfaces: list = field(default_factory=list)

    @property
    def connected(self):
        return bool(self.interfaces)

    @property
    def supported(self):
        return self.provider != "unsupported"


@dataclass
class Reading:
    key: str
    title: str
    subtitle: str = ""
    level: int | None = None
    status: str = ""
    detail: str = ""
    error: str = ""
    extra: str = ""
    state: str = "offline"

    @property
    def ok(self):
        return self.level is not None


KNOWN = {
    "046d:c547": ("罗技 GPW2", "logitech", "GPW2 接收器 · LIGHTSPEED 2.4 GHz"),
    "046d:c094": ("PRO X Wireless", "unsupported", "GPW2 · USB 有线连接"),
    "320f:5088": ("VGN S99 键盘", "vgn_s99", "2.4 GHz 无线键盘"),
    "3837:6045": ("迈从 X9 耳机", "mchose_x9", "2.4 GHz 无线耳机"),
    "373b:1216": ("ATK / VXE 无线鼠标", "vendor", "2.4 GHz 无线鼠标"),
    "3654:6901": ("DARKRIM SHADOW", "darkrim", "2.4 GHz 无线耳机"),
}


def discover_devices():
    """Return the current physical HID inventory, using reported device names."""
    groups = {}
    for interface in hw.enumerate_hid():
        name = interface.product or ""
        if not interface.vid or "virtual" in name.lower():
            continue
        key = f"{interface.vid:04x}:{interface.pid:04x}"
        groups.setdefault(key, []).append(interface)
    devices = {}
    for key, interfaces in groups.items():
        title = hw.pick_device_name(interfaces)
        provider, connection = "unsupported", "已发现设备"
        if key in KNOWN:
            _, provider, connection = KNOWN[key]
            if provider == "vgn_s99":
                from vgn_s99 import is_s99
                if not any(is_s99(interface) for interface in interfaces):
                    title = hw.pick_device_name(interfaces)
                    provider, connection = "unsupported", "已发现设备"
            elif provider == "mchose_x9":
                from mchose_x9 import is_x9
                if not any(is_x9(interface) for interface in interfaces):
                    title = hw.pick_device_name(interfaces)
                    provider, connection = "unsupported", "已发现设备"
        elif any(s.vid == d.vid and s.usage_page == d.usage_page
                 for s in bt.all_protocols() for d in interfaces):
            provider, connection = "vendor", "2.4 GHz 无线设备"
        devices[key] = Device(key, title, provider, connection, interfaces)
    return dict(sorted(devices.items(), key=lambda item: (item[1].title.casefold(), item[0])))


def remembered_title(key, labels=None):
    return (labels or {}).get(key) or KNOWN.get(key, (key,))[0]


def selected_devices(cfg, devices):
    selected = cfg.get("selected_devices")
    if selected is None:
        selected = [key for key, device in devices.items() if device.connected and device.supported][:2]
    slots = list(selected[:2]) + [""] * (2 - len(selected[:2]))
    if slots[0] and slots[0] == slots[1]:
        slots[1] = ""
    return slots


def switch_slot(slots, slot, key):
    result = list(slots)
    other = 1 - slot
    if key and result[other] == key:
        result[other] = result[slot]
    result[slot] = key
    return result


def tray_device_keys(cfg, devices):
    selected = cfg.get("tray_devices")
    if selected is None:
        return [key for key, device in devices.items() if device.connected and device.supported]
    return list(dict.fromkeys(key for key in selected if key))


def read_device(device):
    reading = Reading(device.key, device.title, device.connection)
    if not device.connected:
        reading.error = "设备未连接，请连接后刷新。"
        return reading
    if not device.supported:
        reading.state = "unsupported"
        reading.error = "已识别设备，电量协议待适配。"
        reading.extra = "可以保留在这里，完成适配后即可显示电量。"
        return reading
    try:
        if device.provider == "logitech":
            iface = next((d for d in device.interfaces if d.usage_page == 0xFF00 and d.output_len == 20), None)
            result = bt.strategy_logitech_hidpp(iface, device.interfaces) if iface else None
        elif device.provider == "vgn_s99":
            from vgn_s99 import is_s99
            iface = next((interface for interface in device.interfaces if is_s99(interface)), None)
            result = bt.strategy_vgn_s99(iface, device.interfaces) if iface else None
        elif device.provider == "mchose_x9":
            from mchose_x9 import is_x9
            iface = next((interface for interface in device.interfaces if is_x9(interface)), None)
            result = bt.strategy_mchose_x9(iface, device.interfaces) if iface else None
        elif device.provider == "darkrim":
            info = drp.read_info(timeout=0.35)
            if not info.online or info.battery is None:
                reading.error = info.detail or "耳机未连接，请开机后刷新。"
                return reading
            reading.level = info.battery
            reading.status = "已连接"
            reading.state = "ready"
            reading.detail = "DARKRIM 无线电量"
            return reading
        else:
            result = None
            for iface in device.interfaces:
                candidate = bt.strategy_vendor_protocol(iface)
                if candidate is not None:
                    result = candidate
                    if candidate.ok:
                        break
        if result is None:
            reading.error = "未找到可用的读取接口。"
        elif not result.ok:
            reading.error = result.detail or "暂时没有收到设备回复。"
        else:
            reading.level = result.level
            reading.status = result.status_text
            reading.detail = result.source
            reading.state = "ready" if result.level is not None else "unavailable"
            if result.level is None:
                reading.error = "设备暂未提供电量百分比。"
                if result.voltage_mv:
                    reading.extra = f"电池电压 {result.voltage_mv} mV"
    except Exception as error:
        reading.error = f"读取失败：{error}"
    return reading


def read_selected(slots, devices, labels=None):
    readings = {}
    for key in dict.fromkeys(k for k in slots if k):
        device = devices.get(key) or Device(key, remembered_title(key, labels))
        readings[key] = read_device(device)
    return readings
