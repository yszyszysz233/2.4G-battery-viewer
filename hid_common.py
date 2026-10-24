# -*- coding: utf-8 -*-
"""hid_common.py —— 各厂商协议模块共用的小工具（纯函数 + 一次“发请求 / 收回复”）

为了和 hidapi 写的参考实现（HeadsetControl / HaloBattery 等）逐字节对齐，这里约定：

* **写**：data[0] 是 Report ID（设备不用 Report ID 时为 0x00），
  不足 OutputReportByteLength 的部分补 0 —— Windows 的 WriteFile 要求长度刚好等于它，
  hidapi 也是这样补齐的。优先用 WriteFile（中断 OUT，和 hidapi 的 hid_write 一致），
  失败再退回 HidD_SetOutputReport（控制传输）。
* **读**：Windows ReadFile 返回的 buf[0] 总是 Report ID。hidapi 在 Report ID 为 0 时
  会把这个 0x00 去掉，所以参考实现里的“回复第 n 字节”是去掉 0x00 之后的偏移。
  `hidapi_strip()` 做同样的事；非 0 的 Report ID 原样保留（hidapi 也保留）。
"""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Optional

_MI_RE = re.compile(r"[&#]mi_([0-9a-f]{2})", re.IGNORECASE)


def interface_number(path: str) -> Optional[int]:
    """从设备路径里取 USB 接口号（…&mi_03…）。单接口设备没有 mi_ 段，返回 None。"""
    m = _MI_RE.search(path or "")
    return int(m.group(1), 16) if m else None


def pad(data: Iterable[int], length: int) -> bytes:
    """补 0 到 length（length<=0 时原样返回）；超长时截断。"""
    b = bytes(data)
    if length and length > 0:
        b = (b + b"\x00" * length)[:length]
    return b


def hidapi_strip(report: Optional[bytes]) -> list[int]:
    """把 Windows 原始 Input Report 转成 hidapi hid_read() 的样子。"""
    if not report:
        return []
    r = list(report)
    if r[0] == 0x00:
        r = r[1:]
    return r


def hexdump(data: Optional[Iterable[int]], n: int = 16) -> str:
    if not data:
        return "-"
    d = list(data)
    s = " ".join(f"{b:02x}" for b in d[:n])
    return s + (" …" if len(d) > n else "")


def write_report(dev, data: bytes, output_len: int) -> None:
    """按 hidapi 的方式写一条 Output Report。"""
    frame = pad(data, output_len or len(data))
    try:
        dev.write_output(frame, use_hidd=False)      # WriteFile = 中断 OUT
    except OSError:
        dev.write_output(frame, use_hidd=True)       # 退回 HidD_SetOutputReport


def transact(dev, request: bytes, output_len: int,
             accept: Callable[[list[int]], bool],
             timeout: float = 1.0, strip: bool = True,
             drain: bool = True) -> tuple[Optional[list[int]], Optional[list[int]]]:
    """发一条请求，在 timeout 秒内读回复，返回 (被 accept 接受的回复, 第一条收到的报文)。

    strip=True 时回复先经过 hidapi_strip()。两个返回值都可能是 None。
    """
    if drain:
        try:
            dev.drain()
        except OSError:
            pass
    write_report(dev, request, output_len)
    first = None
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        raw = dev.read_input(timeout_ms=100)
        if not raw:
            time.sleep(0.005)        # read_input 出错时会立刻返回 None，别空转
            continue
        r = hidapi_strip(raw) if strip else list(raw)
        if first is None:
            first = r
        if accept(r):
            return r, first
    return None, first


# --------------------------------------------------------------------------
# 统一的读取结果
# --------------------------------------------------------------------------
from dataclasses import dataclass, field   # noqa: E402


@dataclass
class DeviceBattery:
    """一次读取的结果。

    found   = 找到了这一族的接收器 / 设备
    online  = 设备回了话且确认在线（耳机开机、和接收器连上了）
    level   = 0-100；None 表示没有读到（离线 / 不支持 / 充电时不可靠）
    charging= True / False；None 表示协议里没有这个信息
    approx  = 电量只有几档（例如 0-4 档），显示时加“约”
    """
    found: bool = False
    online: bool = False
    level: Optional[int] = None
    charging: Optional[bool] = None
    model: str = ""
    vid: int = 0
    pid: int = 0
    approx: bool = False
    voltage_mv: Optional[int] = None
    detail: str = ""
    diag: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.level is not None
