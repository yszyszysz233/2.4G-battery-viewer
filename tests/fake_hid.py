# -*- coding: utf-8 -*-
"""在非 Windows 上测试用：装一个假的 hid_win 模块，并提供可编程的假设备。"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Callable, Optional


class HidError(OSError):
    pass


@dataclass
class FakeIface:
    path: str
    vid: int
    pid: int
    usage_page: int = 0xFF00
    usage: int = 0x0001
    input_len: int = 64
    output_len: int = 64
    feature_len: int = 0
    product: str = ""
    manufacturer: str = ""
    serial: str = ""
    version: int = 0
    error: str = ""

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def reserved(self) -> bool:
        return False

    def describe_usage(self) -> str:
        return f"0x{self.usage_page:04X}:0x{self.usage:04X}"


def path(vid: int, pid: int, mi: Optional[int] = None, col: int = 1) -> str:
    seg = f"&mi_{mi:02x}" if mi is not None else ""
    return (rf"\\?\hid#vid_{vid:04x}&pid_{pid:04x}{seg}&col{col:02d}"
            r"#7&1234&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}")


class FakeDevice:
    """responder(frame: bytes) -> list[bytes]：每写一帧，把返回的报文排进输入队列。
    feature_responder(frame) -> bytes：set_feature 之后 get_feature 返回什么。"""

    responder: Callable = staticmethod(lambda frame: [])
    feature_responder: Callable = staticmethod(lambda frame: b"")
    write_ok = True
    writes: list = []
    opened: list = []

    def __init__(self, path: str, write: bool = False):
        self.path = path
        self.write_access = write and FakeDevice.write_ok
        self.queue: list = []
        self.feature = b""
        FakeDevice.opened.append(path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        pass

    def drain(self):
        self.queue.clear()

    def write_output(self, data: bytes, use_hidd: bool = True):
        FakeDevice.writes.append(bytes(data))
        self.queue.extend(FakeDevice.responder(bytes(data)))

    def read_input(self, timeout_ms: int = 200, length=None, stop_flag=None):
        return self.queue.pop(0) if self.queue else None

    def set_feature_report(self, data: bytes):
        FakeDevice.writes.append(bytes(data))
        self.feature = FakeDevice.feature_responder(bytes(data))

    def get_feature_report(self, report_id: int, length=None):
        return self.feature

    def value_caps(self, report_type=2):
        return []

    def usage_value(self, report_type, report, caps):
        return None

    @classmethod
    def reset(cls):
        cls.responder = staticmethod(lambda frame: [])
        cls.feature_responder = staticmethod(lambda frame: b"")
        cls.write_ok = True
        cls.writes = []
        cls.opened = []


def install() -> types.ModuleType:
    mod = sys.modules.get("hid_win")
    if mod is not None and getattr(mod, "_FAKE", False):
        return mod
    mod = types.ModuleType("hid_win")
    mod._FAKE = True
    mod.HidError = HidError
    mod.HidDevice = FakeDevice
    mod.HidInterface = FakeIface
    mod.HidP_Input, mod.HidP_Output, mod.HidP_Feature = 0, 1, 2
    mod.BATTERY_USAGES = []
    mod.SYSTEM_RESERVED_USAGES = set()
    mod.IFACES = []
    mod.enumerate_hid = lambda: list(mod.IFACES)

    def _missing(name):          # 其它 Windows 专用的名字：给个占位
        if name.startswith("__"):
            raise AttributeError(name)
        return object()
    mod.__getattr__ = _missing
    sys.modules["hid_win"] = mod
    return mod
