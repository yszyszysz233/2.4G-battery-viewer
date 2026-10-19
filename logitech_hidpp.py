"""Read-only Logitech HID++ queries shared by the panel, tray and diagnostics."""
from __future__ import annotations

import secrets
import time
from contextlib import ExitStack
from dataclasses import dataclass, field

import hid_win as hw
from hid_lock import device_lock


class HidppError(hw.HidError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


@dataclass
class BatteryState:
    level: int | None
    status: str
    feature: int
    raw: bytes
    voltage_mv: int | None = None
    extra: dict = field(default_factory=dict)


STATUS = {0: "discharging", 1: "charging", 2: "charging", 3: "full", 4: "charging"}


class HidppSession:
    def __init__(self, interfaces, index=1, trace=None):
        self.interfaces = sorted(
            (d for d in interfaces if d.vid == 0x046D and d.usage_page == 0xFF00
             and d.input_len in (7, 20) and d.output_len in (7, 20)),
            key=lambda d: d.output_len, reverse=True,
        )
        self.index = index
        self.trace = trace or (lambda *_: None)
        self.sequence = secrets.randbelow(15)
        self.stack = ExitStack()

    def __enter__(self):
        if not self.interfaces or self.interfaces[0].output_len != 20:
            raise HidppError("没有找到罗技 HID++ 通信接口")
        if sum(d.output_len == 20 for d in self.interfaces) != 1:
            raise HidppError("检测到多个同型号接收器，请暂时只连接一个")
        try:
            # The panel and tray must not query the same receiver concurrently.
            self.stack.enter_context(device_lock(self.interfaces[0].vid_pid))
            self.devices = [self.stack.enter_context(hw.HidDevice(d.path, write=True))
                            for d in self.interfaces]
            for device in self.devices:
                device.drain()
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        self.stack.close()

    def call(self, feature, function=0, params=b"", attempts=3, timeout=1.0):
        for _ in range(attempts):
            self.sequence = self.sequence % 15 + 1
            function_id = function << 4 | self.sequence
            frame = (bytes([0x11, self.index, feature, function_id]) + bytes(params)).ljust(20, b"\0")
            if len(frame) != 20:
                raise ValueError("HID++ 请求参数过长")
            self.trace("TX", frame)
            self.devices[0].write_output(frame, use_hidd=False)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                for device in self.devices:
                    reply = device.read_input(timeout_ms=15)
                    if not reply or len(reply) < 4 or reply[0] not in (0x10, 0x11) or reply[1] != self.index:
                        continue
                    if reply[2] == feature and reply[3] == function_id:
                        self.trace("RX", reply)
                        return reply
                    if (len(reply) >= 6 and reply[2] in (0xFF, 0x8F)
                            and reply[3] == feature and reply[4] == function_id):
                        self.trace("ERR", reply)
                        raise HidppError(f"设备返回 HID++ 错误 0x{reply[5]:02X}", reply[5])
        raise HidppError("鼠标没有回复，请确认已开机，移动鼠标后刷新")

    def feature_index(self, feature_id):
        reply = self.call(0, params=feature_id.to_bytes(2, "big"))
        if len(reply) < 7:
            raise HidppError("特性查询回复不完整")
        return reply[4]

    def device_name(self):
        index = self.feature_index(0x0005)
        if not index:
            return "Logitech device"
        reply = self.call(index)[4:]
        if not reply or not 0 < reply[0] <= 100:
            raise HidppError("设备名称长度无效")
        length, name = reply[0], b""
        while len(name) < length:
            part = self.call(index, 1, [len(name)])[4:]
            if not part:
                raise HidppError("设备名称回复不完整")
            name += part[:length - len(name)]
        return name.decode("utf-8", errors="replace")

    def battery(self):
        for feature in (0x1004, 0x1000, 0x1001):
            index = self.feature_index(feature)
            if not index:
                continue
            reply = self.call(index, 1 if feature == 0x1004 else 0)
            return parse_battery(feature, reply, index)
        raise HidppError("设备没有可用的 HID++ 电量特性")


def parse_battery(feature, reply, feature_index=0):
    if len(reply) < (8 if feature == 0x1004 else 7):
        raise HidppError("电量回复不完整")
    extra = {"feature_index": feature_index}
    if feature in (0x1004, 0x1000):
        level = reply[4]
        if level > 100:
            raise HidppError("设备上报的电量超出有效范围")
        status = STATUS.get(reply[6], "unknown")
        extra.update(status_code=reply[6], level_flags=reply[5])
        # Feature 1000 uses zero to indicate an unavailable percentage.
        if feature == 0x1000 and level == 0:
            level = None
        return BatteryState(level, status, feature, reply, extra=extra)
    mv = int.from_bytes(reply[4:6], "big")
    if not 2000 <= mv <= 5000:
        raise HidppError("设备上报的电池电压无效")
    flags = reply[6]
    status = "discharging"
    if flags & 0x80:
        status = {0: "charging", 1: "full", 2: "discharging"}.get(flags & 3, "unknown")
    return BatteryState(None, status, feature, reply, mv, extra)


def read_battery(interfaces, index=1, trace=None):
    with HidppSession(interfaces, index=index, trace=trace) as session:
        result = session.battery()
        try:
            result.extra["device_name"] = session.device_name()
        except HidppError:
            pass  # A missing optional name must not discard a valid battery.
        result.extra["device_index"] = index
        return result
