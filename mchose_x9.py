"""MCHOSE X9 2.4G battery query, verified on receiver 3837:6045.

Protocol checked against the installed official MCHOSE HUB renderer:
webHID-BfJvRhUp.js, getHeadphonesBatteryInfo and its input-report listener.
Only the battery query is sent; no audio, lighting or firmware writes.
"""
from __future__ import annotations

import argparse
import time

import hid_win as hw
from battery import BatteryResult
from hid_lock import device_lock

QUERY = bytes.fromhex("55 65 01").ljust(64, b"\0")
SOURCE = "MCHOSE X9 · 2.4G 电量"


def is_x9(interface):
    return (interface.vid == 0x3837 and interface.pid == 0x6045
            and (interface.product or "").strip().lower() == "mchose x9"
            and interface.usage_page == 0xFF90 and interface.usage == 1
            and interface.input_len == 64 and interface.output_len == 64
            and interface.feature_len == 0)


def parse_reply(report):
    if len(report) != 64 or not report.startswith(b"\x55\x65"):
        return None
    level, state = report[2:4]
    if state == 0x1A:
        # The receiver can reply while the headset is sleeping/disconnected.
        # Its level byte must not become a live reading in that state.
        return BatteryResult(ok=False, source=SOURCE, raw=report,
                             detail="耳机休眠或未连接，请开机并切到 2.4G 模式后刷新")
    if level > 100:
        raise ValueError(f"耳机上报的电量 {level} 超出有效范围")
    status = {2: "discharging", 3: "charging", 4: "full"}.get(state, "unknown")
    return BatteryResult(
        ok=True, level=level, raw_level=level, status=status, source=SOURCE,
        detail="接收器 3837:6045 · 设备上报百分比", raw=report,
        extra={"status_raw": state},
    )


def read_battery(interfaces=None, trace=None):
    interfaces = hw.enumerate_hid() if interfaces is None else interfaces
    candidates = [interface for interface in interfaces if is_x9(interface)]
    if not candidates:
        return BatteryResult(ok=False, source=SOURCE, detail="没有找到迈从 X9 的 2.4G 接收器")
    if len(candidates) != 1:
        return BatteryResult(ok=False, source=SOURCE, detail="检测到多个 X9 接收器，请暂时只连接一个")
    trace = trace or (lambda *_: None)
    invalid = ""
    try:
        with device_lock("3837:6045"), hw.HidDevice(candidates[0].path, write=True) as device:
            device.drain()  # Open the separate input handle before the request.
            for _ in range(3):
                trace("TX", QUERY)
                device.write_output(QUERY, use_hidd=False)
                deadline = time.monotonic() + 1.0
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
                         detail=invalid or "耳机没有回复，请确认耳机已开机并连接到 2.4G 接收器")


def main():
    parser = argparse.ArgumentParser(description="迈从 X9 2.4G 电量诊断")
    parser.add_argument("--debug", action="store_true", help="显示电量查询及设备回复")
    args = parser.parse_args()

    def trace(direction, report):
        if args.debug:
            print(direction, report.hex(" "), flush=True)
    result = read_battery(trace=trace)
    print(f"迈从 X9: {result.level}% · {result.status_text}" if result.ok else result.detail)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
