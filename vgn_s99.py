"""Verified VGN S99 2.4G battery query; no configuration/lighting writes.

Protocol reference:
https://github.com/JKWTCN/BatteryMonitor/blob/master/src/providers/hid/VgnHidProvider.cpp
Independently implemented and verified against the local 320F:5088 S99 receiver.
"""
from __future__ import annotations

import argparse
import time

import hid_win as hw
from battery import BatteryResult
from hid_lock import device_lock

QUERY = bytes.fromhex("04 00 00 1A 06 00 00 00")
SOURCE = "VGN S99 · 2.4G 电量"


def is_s99(interface):
    # 320F:5088 is shared with other keyboard models; verify the product too.
    return (interface.vid == 0x320F and interface.pid == 0x5088
            and "vgn s99" in (interface.product or "").lower()
            and interface.usage_page == 0xFF1C and interface.usage == 0x0092
            and interface.input_len == 64 and interface.output_len == 64)


def parse_reply(report):
    if len(report) < 10 or not report.startswith(QUERY):
        return None  # Ignore unrelated input, including ordinary key reports.
    level, charge = report[8:10]
    if level > 100:
        raise ValueError(f"键盘上报的电量 {level} 超出有效范围")
    status = {0: "discharging", 1: "charging"}.get(charge, "unknown")
    return BatteryResult(
        ok=True, level=level, raw_level=level, status=status, source=SOURCE,
        detail="接收器 320F:5088 · 设备上报百分比", raw=report,
        extra={"charging_raw": charge},
    )


def read_battery(interfaces=None, trace=None):
    interfaces = hw.enumerate_hid() if interfaces is None else interfaces
    candidates = [interface for interface in interfaces if is_s99(interface)]
    if not candidates:
        return BatteryResult(ok=False, source=SOURCE, detail="没有找到 VGN S99 的 2.4G 接收器")
    if len(candidates) != 1:
        return BatteryResult(ok=False, source=SOURCE, detail="检测到多个 S99 接收器，请暂时只连接一个")
    trace = trace or (lambda *_: None)
    invalid = ""
    try:
        with device_lock("320F:5088"), hw.HidDevice(candidates[0].path, write=True) as device:
            # Use a separate overlapped input handle, opened before sending.
            device.drain()
            request = QUERY.ljust(64, b"\0")
            for _ in range(3):
                trace("TX", request)
                device.write_output(request, use_hidd=False)
                deadline = time.monotonic() + 0.65
                while time.monotonic() < deadline:
                    remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
                    reply = device.read_input(timeout_ms=min(100, remaining_ms))
                    if not reply:
                        continue
                    trace("RX", reply)
                    try:
                        result = parse_reply(reply)
                    except ValueError as error:
                        invalid = str(error)
                        continue
                    if result is not None:
                        return result
    except OSError as error:
        return BatteryResult(ok=False, source=SOURCE, detail=f"读取失败：{error}")
    return BatteryResult(ok=False, source=SOURCE,
                         detail=invalid or "键盘没有回复，请按任意键唤醒后刷新")


def main():
    parser = argparse.ArgumentParser(description="VGN S99 2.4G 电量诊断")
    parser.add_argument("--debug", action="store_true", help="显示只读查询及设备回复")
    args = parser.parse_args()

    def trace(direction, report):
        if args.debug:
            print(direction, report.hex(" "), flush=True)
    result = read_battery(trace=trace)
    print(f"VGN S99: {result.level}% · {result.status_text}" if result.ok else result.detail)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
