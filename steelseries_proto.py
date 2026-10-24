# -*- coding: utf-8 -*-
"""steelseries_proto.py —— 赛睿（SteelSeries, VID 0x1038）无线耳机电量读取

不需要 SteelSeries GG，可以和 GG 同时运行。

协议来源（只用了公开资料里的事实，没有自己编字节）：
  [HC]   Sapd/HeadsetControl  https://github.com/Sapd/HeadsetControl
         commit 2c6790b63ae8220f7b35d7439cd9ea165ca58751
         lib/devices/steelseries_arctis_*.hpp、steelseries_arctis_nova_*.hpp、
         lib/devices/protocols/steelseries_protocol.hpp
  [HALO] HaloBattery  providers/steelseries.py（MIT）
         commit 2251f0534a8e886c9692ead42056059b4f69d8c0
         —— 在 [HC] 的基础上补了“link 字节 / 只认 b0 回复”等真机校验（22A1 实测）
  [LINUX] Linux 内核 drivers/hid/hid-steelseries.c（Arctis 1 / Arctis 7 2018 的状态字节）

所有偏移都是 **hidapi 风格**：去掉 Report ID 0x00 之后的回复（见 hid_common.hidapi_strip）。

一、“b0 状态查询”家族（Nova 7 / Nova 5 / Nova 3P / Arctis 7+ / GameBuds）
    接口：UsagePage 0xFFC0、Usage 0x0001（一般在 USB 接口 3）
    请求：00 b0（Report ID 0 + 0xB0）
    回复首字节必须是 b0，否则不是电量回复（避免把杂报文当电量）
      Nova 7    : b0 <link> <电量> <状态>   link 03=已连接；状态 00=关机/超距，01/02=充电，03=电池
                  新固件电量 0..100，老固件（部分 PID）0..4 档
      Nova 5/3P : b0 <状态> <?> <电量 0..100> <充电>   状态 02=关机；充电 01=充电中
      Arctis 7+ : b0 <状态> <电量 0..4> <充电>          状态 01=关机；充电 01=充电中
      GameBuds  : b0 <?> <?> <左状态> <右状态> <左%> <右%>   状态 03=耳机在盒外

二、老款 Arctis 与 Nova Pro Wireless（每款自己的请求）
      Arctis 1 / 1 Xbox / 7X / 7P : 接口 3，UsagePage 0xFF43；06 12 → 06 12 <状态> <电量>，状态 01=关机
      Arctis 7 (2018, 12AD)       : 接口 5；06 14 → 06 14 <link>（03=已连接），再 06 18 → 06 18 <电量>
      Arctis 7 (2019) / Pro 2019  : 接口 5；06 18 → 06 18 <电量>（0 视为关机）
      Arctis 9                    : 接口 0；00 20 → aa 01 <?> <原始电量 0x64..0x9A> <充电>
      Arctis Pro Wireless         : 接口 0；41 aa → <02 关机 / 04 在线>，再 40 aa → <0..4 档>
      Nova Pro Wireless / X       : 接口 4（或 3）；06 b0 → 字节 6 = 0..8 档，字节 15 = 01 关机 / 02 充电 / 08 电池
"""

from __future__ import annotations

from typing import Callable, Optional, Tuple

import hid_common as hc
from hid_common import DeviceBattery

VID = 0x1038
B0_USAGE_PAGE = 0xFFC0
B0_USAGE = 0x0001
B0_INTERFACE = 3
B0_REQUEST = bytes([0x00, 0xB0])
TIMEOUT = 1.0

# (电量, 充电中(None=协议里没有这个信息), 在线)
Reading = Tuple[Optional[int], Optional[bool], bool]
OFF: Reading = (None, None, False)


# ---------------------------------------------------------------------------
# 一、b0 家族解析（纯函数，输入是 hidapi 风格的回复）
# ---------------------------------------------------------------------------
def parse_nova7(r) -> Reading:
    # link 字节必须是 03：耳机关机时 dongle 会一直重复上次的电量 [HALO]
    if len(r) < 4 or r[0] != 0xB0 or r[1] != 0x03 or r[3] == 0x00:
        return OFF
    return min(r[2], 100), r[3] in (0x01, 0x02), True


def parse_nova7_discrete(r) -> Reading:
    level, chg, online = parse_nova7(r)
    return (None if level is None else min(level, 4) * 25), chg, online


def parse_nova5(r) -> Reading:
    if len(r) < 5 or r[0] != 0xB0 or r[1] == 0x02:
        return OFF
    return min(r[3], 100), r[4] == 0x01, True


def parse_arctis7_plus(r) -> Reading:
    if len(r) < 4 or r[0] != 0xB0 or r[1] == 0x01:
        return OFF
    return min(r[2], 4) * 25, r[3] == 0x01, True


def parse_gamebuds(r) -> Reading:
    """两只耳塞里取在盒外（状态 03）的较低电量；都在盒里 = 离线。"""
    if len(r) < 7 or r[0] != 0xB0:
        return OFF
    levels = [r[5 + i] for i in (0, 1) if r[3 + i] == 0x03]
    if not levels:
        return OFF
    return min(min(levels), 100), None, True


# PID -> (型号, 解析函数, 是否只有几档)
B0_MODELS: dict[int, tuple[str, Callable, bool]] = {
    0x22A1: ("Arctis Nova 7", parse_nova7, False),
    0x2202: ("Arctis Nova 7", parse_nova7_discrete, True),
    0x227E: ("Arctis Nova 7 Gen 2", parse_nova7, False),
    0x2206: ("Arctis Nova 7x", parse_nova7_discrete, True),
    0x2258: ("Arctis Nova 7x", parse_nova7, False),
    0x229E: ("Arctis Nova 7x", parse_nova7, False),
    0x22AD: ("Arctis Nova 7x", parse_nova7, False),
    0x22A4: ("Arctis Nova 7X", parse_nova7_discrete, True),
    0x22A5: ("Arctis Nova 7X", parse_nova7, False),
    0x223A: ("Arctis Nova 7 Diablo IV", parse_nova7_discrete, True),
    0x22A9: ("Arctis Nova 7 Diablo IV", parse_nova7, False),
    0x227A: ("Arctis Nova 7 WoW Edition", parse_nova7_discrete, True),
    0x220A: ("Arctis Nova 7P", parse_nova7_discrete, True),
    0x22A7: ("Arctis Nova 7P", parse_nova7, False),
    0x2298: ("Arctis Nova 7P", parse_nova7, False),
    0x2232: ("Arctis Nova 5", parse_nova5, False),
    0x2253: ("Arctis Nova 5X", parse_nova5, False),
    0x2269: ("Arctis Nova 3P Wireless", parse_nova5, False),
    0x226D: ("Arctis Nova 3X Wireless", parse_nova5, False),
    0x220E: ("Arctis 7+", parse_arctis7_plus, True),
    0x2212: ("Arctis 7+ PS5", parse_arctis7_plus, True),
    0x2216: ("Arctis 7+ Xbox", parse_arctis7_plus, True),
    0x2236: ("Arctis 7+ Destiny", parse_arctis7_plus, True),
    0x230A: ("Arctis GameBuds", parse_gamebuds, False),
    0x2317: ("Arctis GameBuds X", parse_gamebuds, False),   # [HALO] #146，GG 对照验证
}


# ---------------------------------------------------------------------------
# 二、老款 / Nova Pro：多步交换。ask(request, accept) -> 回复或 None
# ---------------------------------------------------------------------------
Ask = Callable[[bytes, Callable[[list], bool]], Optional[list]]


def _echo(a: int, b: int) -> Callable[[list], bool]:
    return lambda r: len(r) >= 4 and r[0] == a and r[1] == b


def exchange_arctis1(ask: Ask) -> Reading:
    r = ask(bytes([0x06, 0x12]), _echo(0x06, 0x12))
    if r is None or r[2] == 0x01:
        return OFF
    return min(r[3], 100), None, True


def exchange_arctis7_2018(ask: Ask) -> Reading:
    r = ask(bytes([0x06, 0x14]), _echo(0x06, 0x14))
    if r is None or r[2] != 0x03:
        return OFF
    r = ask(bytes([0x06, 0x18]), _echo(0x06, 0x18))
    if r is None:
        return OFF
    return min(r[2], 100), None, True


def exchange_arctis7(ask: Ask) -> Reading:
    r = ask(bytes([0x06, 0x18]), _echo(0x06, 0x18))
    if r is None or r[2] == 0:
        return OFF
    return min(r[2], 100), None, True


def exchange_arctis9(ask: Ask) -> Reading:
    r = ask(bytes([0x00, 0x20]), lambda r: len(r) >= 5 and r[0] in (0xAA, 0x55))
    if r is None or r[0] != 0xAA or r[1] != 0x01:
        return OFF
    raw = min(max(r[3], 0x64), 0x9A)
    return (raw - 0x64) * 100 // (0x9A - 0x64), r[4] == 0x01, True


def exchange_pro_wireless(ask: Ask) -> Reading:
    r = ask(bytes([0x41, 0xAA]), lambda r: len(r) >= 1 and r[0] in (0x02, 0x04))
    if r is None or r[0] == 0x02:
        return OFF
    r = ask(bytes([0x40, 0xAA]), lambda r: len(r) >= 1 and r[0] <= 4)
    if r is None:
        return OFF
    return r[0] * 25, None, True


NOVA_PRO_OFF, NOVA_PRO_CHARGING, NOVA_PRO_ONLINE = 0x01, 0x02, 0x08


def parse_nova_pro(r) -> Reading:
    if (r is None or len(r) < 16 or r[15] not in (NOVA_PRO_OFF, NOVA_PRO_CHARGING, NOVA_PRO_ONLINE)
            or r[15] == NOVA_PRO_OFF or not 0 <= r[6] <= 8):
        return OFF
    return r[6] * 100 // 8, r[15] == NOVA_PRO_CHARGING, True


def exchange_nova_pro(ask: Ask) -> Reading:
    r = ask(bytes([0x06, 0xB0]),
            lambda r: len(r) >= 16 and r[15] in (NOVA_PRO_OFF, NOVA_PRO_CHARGING, NOVA_PRO_ONLINE))
    return parse_nova_pro(r)


# PID -> (型号, 接口号元组, UsagePage 或 None(=任意厂商页 >=0xFF00), 交换函数, 是否只有几档)
CLASSIC_MODELS: dict[int, tuple[str, tuple, Optional[int], Callable[[Ask], Reading], bool]] = {
    0x12B3: ("Arctis 1 Wireless", (3,), 0xFF43, exchange_arctis1, False),
    0x12B6: ("Arctis 1 Wireless Xbox", (3,), 0xFF43, exchange_arctis1, False),
    0x12D7: ("Arctis 7X", (3,), 0xFF43, exchange_arctis1, False),
    0x12D5: ("Arctis 7P", (3,), 0xFF43, exchange_arctis1, False),
    0x12AD: ("Arctis 7 (2018)", (5,), None, exchange_arctis7_2018, False),
    0x1260: ("Arctis 7 (2019)", (5,), None, exchange_arctis7, False),
    0x1252: ("Arctis Pro Wireless 2019", (5,), None, exchange_arctis7, False),
    0x12C2: ("Arctis 9", (0,), None, exchange_arctis9, False),
    0x1290: ("Arctis Pro Wireless", (0,), None, exchange_pro_wireless, True),
    0x12E0: ("Arctis Nova Pro Wireless", (4, 3), None, exchange_nova_pro, True),
    0x12E5: ("Arctis Nova Pro Wireless X", (4, 3), None, exchange_nova_pro, True),
}

SUPPORTED_PIDS = set(B0_MODELS) | set(CLASSIC_MODELS)


def model_name(pid: int) -> str:
    if pid in B0_MODELS:
        return B0_MODELS[pid][0]
    if pid in CLASSIC_MODELS:
        return CLASSIC_MODELS[pid][0]
    return f"未知型号 1038:{pid:04X}"


# ---------------------------------------------------------------------------
# 接口挑选（纯函数，便于测试）
# ---------------------------------------------------------------------------
def pick_b0_interface(ifaces: list):
    mine = [i for i in ifaces if (i.usage_page, i.usage) == (B0_USAGE_PAGE, B0_USAGE)]
    for i in mine:
        if hc.interface_number(i.path) == B0_INTERFACE:
            return i
    return mine[0] if mine else None


def classic_candidates(ifaces: list, pid: int) -> list:
    _, nums, page, _, _ = CLASSIC_MODELS[pid]
    out = []
    for n in nums:               # 按偏好顺序（Nova Pro 先接口 4 再接口 3）
        for i in ifaces:
            if hc.interface_number(i.path) != n or not i.output_len:
                continue
            if page is not None and i.usage_page != page:
                continue
            if page is None and i.usage_page < 0xFF00:
                continue
            out.append(i)
    return out


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------
def _open(iface):
    from hid_win import HidDevice
    dev = HidDevice(iface.path, write=True)
    if not dev.write_access:
        dev.close()
        raise OSError("接口只能只读打开（可能被其它程序独占）")
    return dev


def _make_ask(dev, iface, diag: list, answered: list) -> Ask:
    def ask(request: bytes, accept):
        r, first = hc.transact(dev, request, iface.output_len, accept, timeout=TIMEOUT)
        if r is not None:
            answered.append(True)
            diag.append(f"  {hc.hexdump(request, 2)} -> {hc.hexdump(r, 16)}")
        else:
            diag.append(f"  {hc.hexdump(request, 2)} -> "
                        f"{'无回复' if first is None else '不认识的回复 ' + hc.hexdump(first, 16)}")
        return r
    return ask


def read_b0(iface, diag: list) -> Optional[list]:
    """b0 家族：发 00 b0，优先取首字节为 b0 的回复。"""
    with _open(iface) as dev:
        r, first = hc.transact(dev, B0_REQUEST, iface.output_len,
                               lambda r: len(r) > 0 and r[0] == 0xB0, timeout=TIMEOUT)
    diag.append(f"  00 b0 -> {hc.hexdump(r or first, 16)}")
    return r or first


def _result(pid: int, model: str, reading: Reading, approx: bool, diag: list) -> DeviceBattery:
    level, chg, online = reading
    res = DeviceBattery(found=True, online=online, vid=VID, pid=pid, model=model,
                        approx=approx, diag=diag)
    if online and level is not None:
        res.level = level
        res.charging = chg
        res.detail = "充电中" if chg else ""
    else:
        res.detail = "耳机未开机 / 不在接收范围（接收器在线）"
    return res


def read_device(pid: int, ifaces: list) -> DeviceBattery:
    """读一个 PID 的电量。ifaces = 该 PID 的全部 HID 接口。"""
    diag: list = [f"[SteelSeries] 1038:{pid:04X} {model_name(pid)}"]
    if pid in B0_MODELS:
        model, parse, approx = B0_MODELS[pid]
        iface = pick_b0_interface(ifaces)
        if iface is None:
            return DeviceBattery(found=True, vid=VID, pid=pid, model=model, diag=diag,
                                 detail="没找到 0xFFC0 控制接口（驱动异常？）")
        try:
            r = read_b0(iface, diag)
        except OSError as e:
            return DeviceBattery(found=True, vid=VID, pid=pid, model=model, diag=diag,
                                 detail=f"打开/收发失败：{e}")
        if not r:
            return DeviceBattery(found=True, vid=VID, pid=pid, model=model, diag=diag,
                                 detail="接收器没有回复（耳机关机或接收器休眠）")
        return _result(pid, model, parse(r), approx, diag)

    if pid in CLASSIC_MODELS:
        model, _, _, exchange, approx = CLASSIC_MODELS[pid]
        cands = classic_candidates(ifaces, pid)
        if not cands:
            return DeviceBattery(found=True, vid=VID, pid=pid, model=model, diag=diag,
                                 detail="没找到对应的厂商控制接口")
        last_err = ""
        for iface in cands:
            answered: list = []
            try:
                with _open(iface) as dev:
                    reading = exchange(_make_ask(dev, iface, diag, answered))
            except OSError as e:
                last_err = str(e)
                diag.append(f"  {iface.path}: {e}")
                continue
            if answered:
                return _result(pid, model, reading, approx, diag)
        return DeviceBattery(found=True, vid=VID, pid=pid, model=model, diag=diag,
                             detail=("收发失败：" + last_err) if last_err
                             else "接收器没有回复（耳机关机或接收器休眠）")

    return DeviceBattery(found=True, vid=VID, pid=pid, model=model_name(pid), diag=diag,
                         detail="这个型号没有公开的电量协议，暂不支持")


def read_battery(all_ifaces: Optional[list] = None) -> DeviceBattery:
    """找第一个赛睿设备并读电量。优先已支持的型号、优先在线的那个。"""
    if all_ifaces is None:
        from hid_win import enumerate_hid
        all_ifaces = enumerate_hid()
    mine = [i for i in all_ifaces if i.vid == VID]
    if not mine:
        return DeviceBattery(found=False, vid=VID, detail="没找到赛睿接收器（VID 1038）")
    pids = sorted({i.pid for i in mine}, key=lambda p: (p not in SUPPORTED_PIDS, p))
    best: Optional[DeviceBattery] = None
    for pid in pids:
        res = read_device(pid, [i for i in mine if i.pid == pid])
        if res.ok:
            return res
        if best is None or (pid in SUPPORTED_PIDS and best.pid not in SUPPORTED_PIDS):
            best = res
    return best


if __name__ == "__main__":
    r = read_battery()
    print(r.model, r.level, "充电" if r.charging else "", r.detail)
    print("\n".join(r.diag))
