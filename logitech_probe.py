# -*- coding: utf-8 -*-
"""Query the locally verified GPW2 receiver without changing device settings.

Usage: python logitech_probe.py [--debug] [--json]
Only targets Logitech 046D:C547, vendor page FF00, paired device slot 1.
"""

from __future__ import annotations

import argparse
import json
import sys

import hid_win as hw


STATUS = {
    0: "放电中", 1: "充电中", 2: "充电中（末段）", 3: "已充满",
    4: "充电中（慢速）", 5: "电池类型无效", 6: "温度异常", 7: "充电异常",
}


def read_once(debug=False):
    import logitech_hidpp as lh
    interfaces = [d for d in hw.enumerate_hid() if d.vid == 0x046D and d.pid == 0xC547]
    def trace(direction, frame):
        if debug:
            print(f"{direction} {frame.hex(' ')}", file=sys.stderr, flush=True)
    state = lh.read_battery(interfaces, trace=trace)
    return {
        "name": state.extra.get("device_name", "Logitech device"),
        "receiver": "046D:C547", "device_index": 1,
        "feature": f"0x{state.feature:04X}", **state.extra,
        "level": state.level, "status": STATUS.get(state.extra.get("status_code"), state.status),
        "raw_payload": state.raw[4:].hex(" "),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--debug", action="store_true", help="在控制台显示请求和匹配的回复")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    try:
        result = read_once(args.debug)
    except OSError as error:
        print(f"读取失败：{error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"{result['name']}: {result['level']}% · {result['status']}")
        print(f"接收器 {result['receiver']} · HID++ {result['feature']} · 设备索引 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
