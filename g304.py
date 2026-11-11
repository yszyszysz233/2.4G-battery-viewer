# -*- coding: utf-8 -*-
"""罗技 G304 Lightspeed 电量读取（HID++ 2.0）

接收器：Logitech LIGHTSPEED Receiver ``046D:C53F``
接口：厂商 HID 集合（usage page ``0xFF00``），优先 20 字节长报文
报文：HID++ 2.0，Report ID ``0x11``，设备索引从 1 开始
特性：``0x1000`` 电池状态（主）/ ``0x1004`` 统一电池 / ``0x1001`` 电池电压
"""

from __future__ import annotations

import ctypes
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

import hid_win as hw
from hid_win import HidDevice, HidError, HidInterface

VID = 0x046D
USAGE_PAGE = 0xFF00
REPORT_ID_LONG = 0x11
REPORT_ID_SHORT = 0x10
DEVICE_INDICES = (1, 2, 3, 4, 5, 6)

FEATURE_BATTERY_STATUS = 0x1000
FEATURE_BATTERY_VOLTAGE = 0x1001
FEATURE_UNIFIED_BATTERY = 0x1004
FEATURE_DEVICE_NAME = 0x0005

STATUS_TEXT = {
    0: "放电中",
    1: "充电中",
    2: "充电中（末段）",
    3: "已充满",
    4: "充电中（慢速）",
    5: "电池类型无效",
    6: "温度异常",
    7: "充电异常",
}

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateMutexW.restype = wintypes.HANDLE
_kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                   wintypes.LPCWSTR]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.ReleaseMutex.restype = wintypes.BOOL
_kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

_MUTEX_NAME = "Local\\G304BatteryViewer_read"


@contextmanager
def _device_lock(timeout_ms: int = 2500):
    """跨进程串行化 HID++ 读写，避免面板和托盘互相抢回复。"""
    handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        yield
        return
    result = _kernel32.WaitForSingleObject(handle, timeout_ms)
    acquired = result in (0x00000000, 0x00000080)     # 0 / WAIT_ABANDONED
    try:
        yield
    finally:
        if acquired:
            _kernel32.ReleaseMutex(handle)
        _kernel32.CloseHandle(handle)


@dataclass
class G304Battery:
    ok: bool
    level: Optional[int] = None
    status: str = ""
    name: str = ""
    detail: str = ""
    error: str = ""
    voltage_mv: Optional[int] = None
    device_index: Optional[int] = None


def find_receiver() -> Optional[HidInterface]:
    """找罗技接收器的厂商接口，优先能发 20 字节长报文的那个。"""
    candidates = [
        d for d in hw.enumerate_hid()
        if d.vid == VID and d.usage_page == USAGE_PAGE and d.output_len >= 7
    ]
    if not candidates:
        return None
    # 20 字节是 HID++ 2.0 长报文接口；7 字节是短报文；32 字节接口不响应
    candidates.sort(key=lambda d: (abs(d.output_len - 20), d.output_len))
    return candidates[0]


def _call(dev: HidDevice, rid: int, index: int, feature_index: int,
          function: int, params: list[int], timeout_ms: int = 260) -> Optional[bytes]:
    body = [rid, index, feature_index, ((function & 0x0F) << 4) | 0x01] + list(params)
    length = 20 if rid == REPORT_ID_LONG else 7
    body = (body + [0] * length)[:length]
    try:
        dev.write_output(bytes(body))
    except HidError:
        return None

    deadline = time.monotonic() + timeout_ms / 1000.0
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            return None
        report = dev.read_input(timeout_ms=max(1, int(min(0.15, remain) * 1000)))
        if not report or len(report) < 4:
            return None
        if (report[0] != rid or report[1] != index
                or report[2] != feature_index):
            continue
        if ((report[3] >> 4) & 0x0F) != function:
            continue
        return report


def _feature_index(dev: HidDevice, rid: int, index: int,
                   feature_id: int) -> Optional[int]:
    reply = _call(dev, rid, index, 0x00, 0x00,
                  [feature_id >> 8, feature_id & 0xFF])
    if not reply or _error(reply) is not None:
        return None
    for pos in (6, 4, 5):
        if len(reply) > pos and reply[pos]:
            return reply[pos]
    return None


def _error(reply: bytes) -> Optional[int]:
    if len(reply) >= 5 and reply[3] & 0x80:
        return reply[4]
    return None


def _device_name(dev: HidDevice, rid: int, index: int, fi: int) -> str:
    chunks = []
    for offset in (0, 16, 32):
        reply = _call(dev, rid, index, fi, 0x01, [offset])
        if not reply or len(reply) < 5:
            break
        chunk = bytes(reply[4:20]).split(b"\x00", 1)[0]
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("ascii", "ignore").strip()


def _read_index(dev: HidDevice, rid: int, index: int) -> Optional[G304Battery]:
    fi = _feature_index(dev, rid, index, FEATURE_BATTERY_STATUS)
    if fi:
        reply = _call(dev, rid, index, fi, 0x00, [])
        if reply and _error(reply) is None and len(reply) >= 7:
            raw, status = reply[4], reply[6]
            status_text = STATUS_TEXT.get(status, f"状态 {status}")
            return G304Battery(
                ok=True, level=max(0, min(100, raw)), status=status_text,
                detail=f"HID++ 2.0 特性 0x1000 · 设备索引 {index}",
            )

    fi = _feature_index(dev, rid, index, FEATURE_UNIFIED_BATTERY)
    if fi:
        reply = _call(dev, rid, index, fi, 0x00, [])
        if reply and _error(reply) is None and len(reply) >= 6:
            raw, status = reply[4], reply[5]
            return G304Battery(
                ok=True, level=max(0, min(100, raw)),
                status=STATUS_TEXT.get(status, f"状态 {status}"),
                detail=f"HID++ 2.0 特性 0x1004 · 设备索引 {index}",
            )

    fi = _feature_index(dev, rid, index, FEATURE_BATTERY_VOLTAGE)
    if fi:
        reply = _call(dev, rid, index, fi, 0x00, [])
        if reply and _error(reply) is None and len(reply) >= 6:
            mv = reply[4] | (reply[5] << 8)
            if 1500 <= mv <= 5000:
                return G304Battery(
                    ok=False, level=None, voltage_mv=mv, status="",
                    detail=f"HID++ 2.0 特性 0x1001 · 设备索引 {index}",
                    error=f"鼠标只上报电压 {mv} mV，没有百分比",
                )
    return None


def _probe_receiver(iface: HidInterface, timeout_ms: int) -> Optional[G304Battery]:
    with HidDevice(iface.path, write=True) as dev:
        rid = REPORT_ID_LONG if iface.output_len >= 20 else REPORT_ID_SHORT
        for index in DEVICE_INDICES:
            res = _read_index(dev, rid, index)
            if res is None:
                continue
            res.device_index = index
            name_fi = _feature_index(dev, rid, index, FEATURE_DEVICE_NAME)
            if name_fi:
                res.name = _device_name(dev, rid, index, name_fi)
            return res
    return None


def read_g304(timeout_ms: int = 260) -> G304Battery:
    """读一次 G304 电量。没插接收器 / 鼠标关机时返回可读的错误。"""
    iface = find_receiver()
    if iface is None:
        return G304Battery(
            ok=False,
            error="没找到罗技接收器（请把 2.4G 接收器插上）",
        )

    try:
        for attempt in range(2):
            with _device_lock():
                res = _probe_receiver(iface, timeout_ms)
            if res is not None:
                return res
            if attempt == 0:
                time.sleep(0.12)
    except HidError as e:
        return G304Battery(ok=False, error=f"无法打开罗技接收器：{e}")
    except Exception as e:
        return G304Battery(ok=False, error=f"读取失败：{e}")

    return G304Battery(
        ok=False,
        error="接收器已连接，但鼠标没有响应（动一下鼠标或换电池后再试）",
    )
