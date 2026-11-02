# -*- coding: utf-8 -*-
"""
hid_win.py —— 纯 ctypes 实现的 Windows HID 访问层（零第三方依赖）

只调用 Windows 自带的 setupapi.dll / hid.dll / kernel32.dll，
不需要安装 hidapi、pywinusb、pyusb 等任何第三方库。

提供的能力：
  * 枚举所有 HID 接口（VID / PID / UsagePage / Usage / 报告长度 / 厂商名 / 产品名）
  * 读取 Feature Report（HidD_GetFeature，支持“0 访问权限”打开系统保留设备）
  * 写入 Feature Report / Output Report
  * 通过 overlapping I/O 读取 Input Report（带超时，可中断）
  * 解析 HID 值能力表（HidP_GetValueCaps），用于自动定位“电量”字段
  * 用 HidP_GetUsageValue 从原始报文里取出某个 usage 的数值

Windows 上“鼠标 / 键盘 / 消费者控制”这类顶层集合会被系统独占，
用普通读写权限打不开（CreateFile 返回“拒绝访问”）；业界通用做法是
用 dwDesiredAccess = 0 打开，此时依然可以读属性、能力表和 Feature Report。
"""

from __future__ import annotations

import ctypes
import sys
import time
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

if sys.platform != "win32":
    raise RuntimeError("hid_win.py 仅支持 Windows")

# --------------------------------------------------------------------------
# DLL
# --------------------------------------------------------------------------
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

HidP_Input = 0
HidP_Output = 1
HidP_Feature = 2

HIDP_STATUS_SUCCESS = 0x00110000

# 报告描述符里“电量”相关的标准 usage（UsagePage, Usage, 说明）
BATTERY_USAGES = [
    (0x0006, 0x0020, "Battery Strength（通用设备控制页）"),
    (0x000D, 0x003B, "Battery Strength（数字化仪页，常见于触控笔）"),
    (0x0085, 0x0001, "Absolute State of Charge（电池系统页）"),
    (0x0085, 0x0066, "Relative State of Charge（电池系统页）"),
    (0x0085, 0x0065, "Battery Level（电池系统页）"),
    (0x0006, 0x0029, "RF Signal Strength（无线信号强度）"),
]

# Windows 独占（Exclusive）打开的顶层集合：只能以 0 访问权限打开，
# 无法发送 Output Report。依据《HID clients supported in Windows》：
#   Mouse 0x0001/0x0001-0x0002、Keyboard 0x0001/0x0006-0x0007、
#   笔 0x000D/0x0001-0x0002、触摸屏 0x000D/0x0004、精密触摸板 0x000D/0x0005
# 其余（游戏控制器、消费者控制、系统控制、HID UPS 电池等）都是共享的，可以正常读写。
SYSTEM_RESERVED_USAGES = {
    (0x0001, 0x0001),  # Pointer
    (0x0001, 0x0002),  # Mouse
    (0x0001, 0x0006),  # Keyboard
    (0x0001, 0x0007),  # Keypad
    (0x000D, 0x0001),  # Pen（外置）
    (0x000D, 0x0002),  # Pen（内置）
    (0x000D, 0x0004),  # Touchscreen
    (0x000D, 0x0005),  # Precision Touchpad
}


# --------------------------------------------------------------------------
# 结构体
# --------------------------------------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("DevicePath", ctypes.c_wchar * 1),
    ]


class OVERLAPPED(ctypes.Structure):
    """异步（overlapping）I/O 用。ctypes.wintypes 里没有，手动定义。"""
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _RANGE(ctypes.Structure):
    _fields_ = [
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
    ]


class _NOT_RANGE(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    ]


class _USAGE_UNION(ctypes.Union):
    _fields_ = [
        ("Range", _RANGE),
        ("NotRange", _NOT_RANGE),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", wintypes.LONG),
        ("LogicalMax", wintypes.LONG),
        ("PhysicalMin", wintypes.LONG),
        ("PhysicalMax", wintypes.LONG),
        ("u", _USAGE_UNION),
    ]


class HIDP_BUTTON_CAPS(ctypes.Structure):
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 9),
        ("u", _USAGE_UNION),
    ]


# --------------------------------------------------------------------------
# 函数原型
# --------------------------------------------------------------------------
setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD,
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID),
    wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.WriteFile.restype = wintypes.BOOL
kernel32.WriteFile.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
]
kernel32.ResetEvent.restype = wintypes.BOOL
kernel32.ResetEvent.argtypes = [ctypes.c_void_p]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.ReleaseMutex.restype = wintypes.BOOL
kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
kernel32.GetOverlappedResult.restype = wintypes.BOOL
kernel32.GetOverlappedResult.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
]
kernel32.CancelIoEx.restype = wintypes.BOOL
kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

hid.HidD_GetAttributes.restype = wintypes.BOOL
hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.restype = wintypes.BOOL
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidD_FreePreparsedData.restype = wintypes.BOOL
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
hid.HidP_GetCaps.restype = ctypes.c_long
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidP_GetValueCaps.restype = ctypes.c_long
hid.HidP_GetValueCaps.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p,
]
hid.HidP_GetButtonCaps.restype = ctypes.c_long
hid.HidP_GetButtonCaps.argtypes = [
    ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(wintypes.USHORT), ctypes.c_void_p,
]
hid.HidP_GetUsageValue.restype = ctypes.c_long
hid.HidP_GetUsageValue.argtypes = [
    ctypes.c_int, wintypes.USHORT, wintypes.USHORT, wintypes.USHORT,
    ctypes.POINTER(wintypes.ULONG), ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG,
]
hid.HidD_GetFeature.restype = wintypes.BOOL
hid.HidD_GetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetFeature.restype = wintypes.BOOL
hid.HidD_SetFeature.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
hid.HidD_SetOutputReport.restype = wintypes.BOOL
hid.HidD_SetOutputReport.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]
for _fn in ("HidD_GetProductString", "HidD_GetManufacturerString",
            "HidD_GetSerialNumberString", "HidD_GetPhysicalDescriptor"):
    _f = getattr(hid, _fn)
    _f.restype = wintypes.BOOL
    _f.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG]

# HID 设备接口类 GUID {4D1E55B2-F16F-11CF-88CB-001111000030}
HID_GUID = GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
)


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------
def _wide_string(func, handle, size: int = 512) -> str:
    """调用 HidD_GetXxxString 读取宽字符串。"""
    buf = ctypes.create_string_buffer(size)
    try:
        if not func(handle, buf, size):
            return ""
    except Exception:
        return ""
    return buf.raw.decode("utf-16-le", errors="ignore").split("\x00")[0].strip()


def _parse_vid_pid(path: str) -> tuple[int, int]:
    lower = path.lower()
    vid = pid = 0
    if "vid_" in lower:
        try:
            vid = int(lower.split("vid_")[1][:4], 16)
        except Exception:
            pass
    if "pid_" in lower:
        try:
            pid = int(lower.split("pid_")[1][:4], 16)
        except Exception:
            pass
    return vid, pid


def physical_group_key(path: str) -> str:
    """同一个物理设备的多个接口归到一组。

    设备接口路径形如：
        \\\\?\\hid#vid_373b&pid_1216&mi_02&col01#7&70aa155&0&0000#{guid}
    其中第 2 段（vid/pid）标识设备，mi_XX 是接口号、colXX 是集合号，
    第 3 段是每个接口各自的实例号。因此用第 2 段去掉 mi_/col_ 后作为分组键。
    """
    parts = path.split("#")
    if len(parts) < 2:
        return path
    keep = []
    for p in parts[1].split("&"):
        low = p.lower()
        if low.startswith("col") or low.startswith("mi_"):
            continue
        keep.append(p)
    return "&".join(keep) or parts[1]


GENERIC_NAMES = ("HID 设备", "HID-compliant", "USB Composite Device", "HIDI2C Device")


def pick_device_name(items: list["HidInterface"]) -> str:
    """在一组接口里挑一个最有信息量的名字作为设备名。"""
    scored: list[tuple[int, str]] = []
    for it in items:
        name = it.product.strip()
        if not name:
            continue
        score = len(name)
        if any(g in name for g in GENERIC_NAMES):
            score -= 20
        if it.manufacturer:
            score += 5
        if it.is_vendor_page:
            score += 5
        scored.append((score, name))
    if not scored:
        return items[0].display_name if items else "HID 设备"
    scored.sort(reverse=True)
    return scored[0][1]


@dataclass
class HidInterface:
    """一个 HID 接口（= 一个顶层集合，对应 setupapi 中的一个设备接口）。"""
    path: str
    vid: int
    pid: int
    version: int
    usage_page: int
    usage: int
    input_len: int
    output_len: int
    feature_len: int
    product: str
    manufacturer: str
    serial: str
    num_feature_value_caps: int = 0
    num_input_value_caps: int = 0
    num_feature_button_caps: int = 0
    error: str = ""

    # 用于分组显示
    group: str = field(default="", repr=False)

    @property
    def vid_pid(self) -> str:
        return f"{self.vid:04X}:{self.pid:04X}"

    @property
    def reserved(self) -> bool:
        return (self.usage_page, self.usage) in SYSTEM_RESERVED_USAGES

    @property
    def is_vendor_page(self) -> bool:
        return self.usage_page >= 0xFF00

    @property
    def display_name(self) -> str:
        if self.product:
            return self.product
        if self.manufacturer:
            return f"{self.manufacturer} HID 设备"
        return "HID 设备（无产品名字符串）"

    def describe_usage(self) -> str:
        return f"0x{self.usage_page:04X}:0x{self.usage:04X}"


def enumerate_hid() -> list[HidInterface]:
    """枚举系统里所有 HID 接口。"""
    result: list[HidInterface] = []
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(HID_GUID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if hdev == INVALID_HANDLE_VALUE or not hdev:
        raise OSError("SetupDiGetClassDevsW 失败，err=%d" % ctypes.get_last_error())
    try:
        index = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = setupapi.SetupDiEnumDeviceInterfaces(
                hdev, None, ctypes.byref(HID_GUID), index, ctypes.byref(iface)
            )
            if not ok:
                break
            index += 1

            need = wintypes.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(iface), None, 0, ctypes.byref(need), None
            )
            if not need.value:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0] = 8  # cbSize
            if not setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(iface), buf, need.value, None, None
            ):
                continue
            path = ctypes.cast(
                ctypes.addressof(buf) + 4, ctypes.c_wchar_p
            ).value or ""
            if not path:
                continue

            item = _probe_interface(path)
            item.group = physical_group_key(path)
            result.append(item)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return result


def _probe_interface(path: str) -> HidInterface:
    """打开一个 HID 接口并读取它的属性与能力表。"""
    vid, pid = _parse_vid_pid(path)
    item = HidInterface(
        path=path, vid=vid, pid=pid, version=0,
        usage_page=0, usage=0, input_len=0, output_len=0, feature_len=0,
        product="", manufacturer="", serial="",
    )

    handle = kernel32.CreateFileW(
        path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if handle == INVALID_HANDLE_VALUE or not handle:
        # 少数设备即使 0 权限也打不开
        item.error = "无法打开（%d）" % ctypes.get_last_error()
        return item

    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
            item.vid = attrs.VendorID
            item.pid = attrs.ProductID
            item.version = attrs.VersionNumber

        item.product = _wide_string(hid.HidD_GetProductString, handle)
        item.manufacturer = _wide_string(hid.HidD_GetManufacturerString, handle)
        item.serial = _wide_string(hid.HidD_GetSerialNumberString, handle)

        preparsed = ctypes.c_void_p()
        if hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) and preparsed:
            try:
                caps = HIDP_CAPS()
                if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) == HIDP_STATUS_SUCCESS:
                    item.usage_page = caps.UsagePage
                    item.usage = caps.Usage
                    item.input_len = caps.InputReportByteLength
                    item.output_len = caps.OutputReportByteLength
                    item.feature_len = caps.FeatureReportByteLength
                    item.num_feature_value_caps = caps.NumberFeatureValueCaps
                    item.num_input_value_caps = caps.NumberInputValueCaps
                    item.num_feature_button_caps = caps.NumberFeatureButtonCaps
            finally:
                hid.HidD_FreePreparsedData(preparsed)
    finally:
        kernel32.CloseHandle(handle)
    return item


# --------------------------------------------------------------------------
# 句柄封装
# --------------------------------------------------------------------------
class HidError(OSError):
    pass


@contextmanager
def query_lock(vid: int, pid: int, timeout_ms: int = 3000):
    """让面板、托盘和命令行依次查询同一接收器，避免互相取走回复。"""
    mutex = kernel32.CreateMutexW(None, False, f"Local\\DeviceBatteryQuery_{vid:04X}_{pid:04X}")
    if not mutex:
        raise HidError("无法创建设备查询锁")
    acquired = False
    try:
        acquired = kernel32.WaitForSingleObject(mutex, timeout_ms) in (WAIT_OBJECT_0, 0x80)
        if not acquired:
            raise HidError("设备正在被另一个电量查询占用，请稍后重试")
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)


class HidDevice:
    """一个已打开的 HID 接口句柄。

    默认用 0 访问权限打开（可读属性/能力表/Feature Report，
    即使设备被系统独占）。需要发送 Output Report 时传 write=True。
    """

    def __init__(self, path: str, write: bool = False):
        self.path = path
        self.write_access = write
        access = (GENERIC_READ | GENERIC_WRITE) if write else 0
        h = kernel32.CreateFileW(
            path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
            OPEN_EXISTING, 0, None,
        )
        if (h == INVALID_HANDLE_VALUE or not h) and write:
            # 退一步：只读方式打开
            h = kernel32.CreateFileW(
                path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                OPEN_EXISTING, 0, None,
            )
            self.write_access = False
        if h == INVALID_HANDLE_VALUE or not h:
            err = ctypes.get_last_error()
            raise HidError(f"CreateFileW 失败 (err={err})", err)
        self.handle = h
        self._preparsed = ctypes.c_void_p()
        if not hid.HidD_GetPreparsedData(self.handle, ctypes.byref(self._preparsed)):
            self._preparsed = ctypes.c_void_p()
        self._caps: Optional[HIDP_CAPS] = None
        # 独立的异步读句柄（懒创建，专门用于收 Input Report）
        self._read_handle: Optional[int] = None
        self._read_event: Optional[int] = None

    # -- 生命周期 ------------------------------------------------------
    def close(self) -> None:
        if getattr(self, "handle", None):
            if self._read_handle:
                kernel32.CancelIoEx(self._read_handle, None)
                kernel32.CloseHandle(self._read_handle)
                self._read_handle = None
            if self._read_event:
                kernel32.CloseHandle(self._read_event)
                self._read_event = None
            if self._preparsed:
                hid.HidD_FreePreparsedData(self._preparsed)
                self._preparsed = ctypes.c_void_p()
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- 能力表 --------------------------------------------------------
    @property
    def caps(self) -> HIDP_CAPS:
        if self._caps is None:
            caps = HIDP_CAPS()
            if not self._preparsed:
                raise HidError("没有 preparsed data，无法读取能力表")
            if hid.HidP_GetCaps(self._preparsed, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                raise HidError("HidP_GetCaps 失败")
            self._caps = caps
        return self._caps

    def value_caps(self, report_type: int = HidP_Feature) -> list[HIDP_VALUE_CAPS]:
        """返回指定报告类型的“值”能力表。"""
        if not self._preparsed:
            return []
        length = wintypes.USHORT(0)
        hid.HidP_GetValueCaps(report_type, None, ctypes.byref(length), self._preparsed)
        if not length.value:
            return []
        arr = (HIDP_VALUE_CAPS * length.value)()
        status = hid.HidP_GetValueCaps(
            report_type, ctypes.byref(arr), ctypes.byref(length), self._preparsed
        )
        if status != HIDP_STATUS_SUCCESS:
            return []
        return [arr[i] for i in range(length.value)]

    # -- 报文读写 ------------------------------------------------------
    def get_feature_report(self, report_id: int, length: Optional[int] = None) -> bytes:
        """读取一条 Feature Report，返回的 bytes[0] 是 report id。"""
        if length is None:
            length = self.caps.FeatureReportByteLength or 64
        buf = ctypes.create_string_buffer(length)
        buf[0] = bytes([report_id & 0xFF])
        if not hid.HidD_GetFeature(self.handle, buf, length):
            raise HidError(f"读 Feature Report 0x{report_id:02X} 失败 "
                           f"(err={ctypes.get_last_error()})")
        return buf.raw

    def set_feature_report(self, data: bytes) -> None:
        # Feature IOCTL 可用于零访问权限句柄；雷蛇把命令放在系统独占的鼠标集合上。
        # Output Report / WriteFile 仍必须有读写权限，由 write_output 单独检查。
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if not hid.HidD_SetFeature(self.handle, buf, len(data)):
            raise HidError(f"写 Feature Report 失败 (err={ctypes.get_last_error()})")

    def write_output(self, data: bytes, use_hidd: bool = True) -> None:
        """发送 Output Report（data[0] 必须是 report id）。"""
        if not self.write_access:
            raise HidError("当前句柄没有写权限，无法发送 Output Report")
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        if use_hidd:
            if hid.HidD_SetOutputReport(self.handle, buf, len(data)):
                return
        written = wintypes.DWORD(0)
        if not kernel32.WriteFile(self.handle, buf, len(data), ctypes.byref(written), None):
            raise HidError(f"WriteFile 失败 (err={ctypes.get_last_error()})")

    def read_input(self, timeout_ms: int = 200,
                   length: Optional[int] = None,
                   stop_flag: Optional[Callable[[], bool]] = None) -> Optional[bytes]:
        """读取一条 Input Report，超时返回 None。

        要点（踩过的坑）：Windows 上如果在同一个句柄上「一边阻塞 ReadFile、
        一边发 IOCTL / WriteFile」，HID 驱动层可能直接把进程打崩。
        因此这里用一个**独立的、带 FILE_FLAG_OVERLAPPED 的读句柄**做异步读，
        配合事件 + 超时，既安全又可以随时取消。
        """
        if not self.caps.InputReportByteLength:
            return None
        if not self._open_reader():
            return None
        if length is None:
            length = self.caps.InputReportByteLength

        buf = ctypes.create_string_buffer(length)
        ov = OVERLAPPED()
        ov.hEvent = self._read_event
        read = wintypes.DWORD(0)
        kernel32.ResetEvent(self._read_event)

        ok = kernel32.ReadFile(self._read_handle, buf, length,
                               ctypes.byref(read), ctypes.byref(ov))
        if ok:
            return buf.raw[: read.value]
        if ctypes.get_last_error() != ERROR_IO_PENDING:
            return None

        deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
        try:
            while True:
                if stop_flag is not None and stop_flag():
                    self._cancel_read(ov)
                    return None
                remain = deadline - time.monotonic()
                if remain <= 0:
                    self._cancel_read(ov)
                    return None
                r = kernel32.WaitForSingleObject(
                    self._read_event, int(min(remain, 0.2) * 1000) or 1)
                if r == WAIT_OBJECT_0:
                    done = wintypes.DWORD(0)
                    if kernel32.GetOverlappedResult(
                            self._read_handle, ctypes.byref(ov),
                            ctypes.byref(done), False):
                        return buf.raw[: done.value]
                    return None
        finally:
            pass

    def _cancel_read(self, ov: "OVERLAPPED") -> None:
        """取消挂起的异步读，保证句柄状态干净。"""
        kernel32.CancelIoEx(self._read_handle, ctypes.byref(ov))
        kernel32.WaitForSingleObject(self._read_event, 200)
        done = wintypes.DWORD(0)
        kernel32.GetOverlappedResult(self._read_handle, ctypes.byref(ov),
                                     ctypes.byref(done), False)

    # -- 独立的异步读句柄 ----------------------------------------------
    def _open_reader(self) -> bool:
        if self._read_handle:
            return True
        h = kernel32.CreateFileW(
            self.path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None,
        )
        if h == INVALID_HANDLE_VALUE or not h:
            return False
        ev = kernel32.CreateEventW(None, True, False, None)
        if not ev:
            kernel32.CloseHandle(h)
            return False
        self._read_handle = h
        self._read_event = ev
        return True

    def drain(self) -> None:
        """丢弃驱动里已经堆积的旧报文（发请求前调用）。"""
        if not self.caps.InputReportByteLength:
            return
        for _ in range(64):
            if self.read_input(timeout_ms=8) is None:
                return

    # -- usage 取值 ----------------------------------------------------
    def usage_value(self, report_type: int, report: bytes, caps: HIDP_VALUE_CAPS) -> Optional[int]:
        """按能力表定义，从原始报文里取出该字段的数值。"""
        if not self._preparsed:
            return None
        value = wintypes.ULONG(0)
        usage = caps.u.NotRange.Usage
        status = hid.HidP_GetUsageValue(
            report_type, caps.UsagePage, caps.LinkCollection, usage,
            ctypes.byref(value), self._preparsed,
            ctypes.create_string_buffer(bytes(report), len(report)), len(report),
        )
        if status != HIDP_STATUS_SUCCESS:
            return None
        return int(value.value)


__all__ = [
    "HidInterface", "HidDevice", "HidError", "enumerate_hid",
    "HIDP_VALUE_CAPS", "HIDP_CAPS", "HidP_Input", "HidP_Output", "HidP_Feature",
    "BATTERY_USAGES", "SYSTEM_RESERVED_USAGES", "physical_group_key",
    "pick_device_name",
    "query_lock",
]
