"""GM3323D battery reports. Standard library only; never writes to the device.

Receiver 1D57:FA60, UsagePage 000A / Usage 0000, 5-byte Input Report:
    03 <00|01> 40 <online> <level>
The companion application's 0x4000/0x4001 handler displays level * 10,
accepting levels 1..10. The online byte must be nonzero before using it.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Optional

VID = 0x1D57
PID = 0xFA60
WIRED_PID = 0xFA61
USAGE_PAGE = 0x000A
USAGE = 0
REPORT_LENGTH = 5


@dataclass(frozen=True)
class Reading:
    level: Optional[int] = None
    detail: str = ""
    raw: bytes = b""

    @property
    def ok(self) -> bool:
        return self.level is not None


def matches(iface) -> bool:
    return (iface.vid, iface.pid, iface.usage_page, iface.usage,
            iface.input_len) == (VID, PID, USAGE_PAGE, USAGE, REPORT_LENGTH)


def parse_report(report: bytes) -> Optional[Reading]:
    """Ignore unrelated reports; never turn offline/stale data into a percent."""
    if (len(report) != REPORT_LENGTH or report[0] != 3
            or int.from_bytes(report[1:3], "little") not in (0x4000, 0x4001)):
        return None
    if not report[3]:
        return Reading(detail="鼠标未连接或已休眠，请打开鼠标并移动一下", raw=report)
    if not 1 <= report[4] <= 10:
        return Reading(detail="鼠标尚未上报有效电量，请稍后刷新", raw=report)
    return Reading(level=report[4] * 10, detail="无线连接 · 电量按 10% 分档上报",
                   raw=report)


def read_battery(iface=None, timeout: float = 5.0) -> Reading:
    # Lazy import lets the pure parser be tested on any platform.
    import hid_win as hw

    if timeout <= 0:
        raise ValueError("timeout must be positive")
    if iface is None:
        devices = hw.enumerate_hid()
        iface = next((d for d in devices if matches(d)), None)
        if iface is None:
            wired = any((d.vid, d.pid) == (VID, WIRED_PID) for d in devices)
            return Reading(detail=("检测到有线连接，当前仅验证了 2.4G 接收器电量读取"
                                   if wired else "没找到 GM3323D 接收器，请插入 USB 接收器"))
    elif not matches(iface):
        return Reading(detail="不是受支持的 GM3323D 电量接口")
    try:
        with hw.HidDevice(iface.path, write=False) as dev:
            if not dev._open_reader():
                return Reading(detail="无法打开电量接口，请重新插拔接收器后重试")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                remain_ms = max(1, int((deadline - time.monotonic()) * 1000))
                report = dev.read_input(timeout_ms=remain_ms)
                if report is None:
                    break
                result = parse_report(report)
                if result is not None:
                    return result
    except OSError as exc:
        return Reading(detail=f"读取失败：{exc}")
    return Reading(detail="未收到电量，请移动鼠标唤醒后再刷新（不会沿用旧电量）")


def main() -> int:
    """Read one GM3323D report without probing or writing to other devices."""
    result = read_battery()
    if result.ok:
        print(f"GM3323D: {result.level}% · {result.detail}")
        return 0
    print(f"GM3323D: 未知 · {result.detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
