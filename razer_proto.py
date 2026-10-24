# -*- coding: utf-8 -*-
"""razer_proto.py —— 雷蛇（Razer, VID 0x1532）无线耳机电量读取

雷蛇耳机 **不走** 雷蛇鼠标/键盘那套标准 90 字节协议，每个系列都不一样。
这里只实现有公开资料（抓包 / 开源驱动）的几种，字节全部来自下列来源：

  [OR2862] openrazer PR #2862  driver/razerblackshark_driver.h（BlackShark V2 Pro 2023，“PA”协议）
           https://github.com/openrazer/openrazer/pull/2862
  [OR1280] openrazer issue #1280（BlackShark V2 Pro 2020 的 Synapse 抓包）
           https://github.com/openrazer/openrazer/issues/1280
  [NARI]   Modzeleczek/RazerNariBatteryLevel（Razer Nari，USBPcap 抓包 + libusb 程序）
           https://github.com/Modzeleczek/RazerNariBatteryLevel  commit 0540d423
  [HALO]   HaloBattery providers/blackshark.py / barracuda.py / blackshark2020.py（MIT）
           commit 2251f0534a8e886c9692ead42056059b4f69d8c0
  [BSHS]   justik13/razer-blackshark-v2-hyperspeed-webhid（MIT）README + index.html
           commit aeb0d90b35124671abf614fc0368522bcb8dcb48

一、PA 协议 —— BlackShark V2 Pro (2023)，接收器 1532:0555  [OR2862][HALO]
    厂商接口 UsagePage 0xFF00（也见过 0xFF14），64 字节中断报告，Report ID 0x02
    查询帧：[0]=02 [1]=80 [2]=08 [5]='P' [6]='A' [7]=08 [9]=03(查询) [10]=命令
    远程模式：[0]=02 [1]=80 [2]=07 [5]='P' [6]='A' [7]=0E [9]=02 [10]=E1 [11]=1开/0关
    回复：[0]=02 … [12]=命令回显 [13]=01(ACK) [14]=长度 [15..]=数据
    命令：0x21 电量 0..100；0x2A 充电（非 0 = 插线充电）
    怪癖：链路空闲 ~0.3s 后会打盹，第一帧只用来唤醒（先发一帧“牺牲”的远程模式）

二、PA 协议变体 —— Barracuda Pro，接收器 1532:053A  [HALO，依据真机 Synapse 抓包]
    UsagePage 0xFF00，Report ID 0x01
    查询帧：[0]=01 [1]=80 [2]=08 [3]='P' [4]='A' [5]=08 [6]=08 [7]=03 [8]=命令
    远程模式：[0]=01 [1]=80 [2]=07 [3]='P' [4]='A' [5]=0E [6]=08 [7]=02 [8]=E1 [9]=1/0
    回复：[3..4]='P','I' … [13]=命令回显 [14]=01 回复 / 02 通知 [15]=长度 [16..]=数据

三、BlackShark V2 Pro (2020)，接收器 1532:0528  [OR1280][NARI][HALO]
    UsagePage 0xFF00 Usage 0x0001，64 字节 Feature Report，Report ID 0xFF
    SET_FEATURE ff 0a 00 fd 04 12 f1 02 05（补 0 到 64），约 100ms 后 GET_FEATURE 0xFF
    有效回复以 ff 0f 05 fe 12 04 1f 08 05 开头；ff 01 00 fe … = 没有新回复（耳机关机）
    [11]=电源状态（09 充电中、01/02/03 电池、06 充满） [12:14]=电压 mV（大端，0=离线）
    [14]=电量（只有 100/80/50/30/10 几档；充电中这个值不可靠，[HALO] 选择隐藏）

四、Razer Nari，接收器 1532:051C（接口 5）  [NARI]
    请求同上（同一个 Feature 0xFF 交换），回复 [12:14] = 电压 mV（大端，0 = 耳机没开）
    该来源只给出电压。百分比是按它记录的两个实测点线性估算的：
    3320 mV = 没电关机，3552 mV = 刚充满拔线 —— 所以只是“约”。

五、BlackShark V2 HyperSpeed（MediaTek 方案）  [BSHS]
    1532:0565 = 2.4G 接收器（domain 0x80，作者实机验证）
    1532:056E = USB 有线（domain 0x00）；1532:0566 = 社区报告的变体（实验性）
    UsagePage 0xFF14（备选 0xFF00），Report ID 0x02 + 63 字节载荷 p：
      p[1]=0x60|seq(5 位滚动)  p[5]=0x04(长度)  p[8]=domain  p[9]=命令  p[11]=0(参数个数)
      p[61]=0x02 ^ p[0] ^ … ^ p[60]（XOR 校验，含 Report ID）  p[62]=0
    回复：63 字节，p[61] == p[0]^…^p[60]，用 seq + 命令配对，p[12] = 值
    命令：0x20 无线链路（1 已连接）  0x21 电量 0..100  0x2A 充电（>0 充电中）

不支持（没有可靠公开协议）：Kaira / Kaira Pro（Xbox 无线 / 蓝牙）、Barracuda / Barracuda X、
Kraken V3 / V4 Pro、BlackShark V3 Pro、Nari Ultimate / Essential 等。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

import hid_common as hc
from hid_common import DeviceBattery

VID = 0x1532
REPORT_LEN = 64

CMD_LINK = 0x20
CMD_BATTERY = 0x21
CMD_CHARGING = 0x2A
CMD_REMOTE = 0xE1

# ---------------------------------------------------------------------------
# 型号表
# ---------------------------------------------------------------------------
PA_2023_PIDS = {0x0555: "BlackShark V2 Pro (2023)"}
BARRACUDA_PRO_PIDS = {0x053A: "Barracuda Pro"}
BS2020_PIDS = {0x0528: "BlackShark V2 Pro (2020)"}
NARI_PIDS = {0x051C: "Nari"}
HYPERSPEED_PIDS = {
    0x0565: ("BlackShark V2 HyperSpeed", 0x80),
    0x056E: ("BlackShark V2 HyperSpeed（有线）", 0x00),
    0x0566: ("BlackShark V2 HyperSpeed（变体，实验性）", 0x00),
}

SUPPORTED_PIDS = (set(PA_2023_PIDS) | set(BARRACUDA_PRO_PIDS) | set(BS2020_PIDS)
                  | set(NARI_PIDS) | set(HYPERSPEED_PIDS))


def model_name(pid: int) -> str:
    for table in (PA_2023_PIDS, BARRACUDA_PRO_PIDS, BS2020_PIDS, NARI_PIDS):
        if pid in table:
            return "Razer " + table[pid]
    if pid in HYPERSPEED_PIDS:
        return "Razer " + HYPERSPEED_PIDS[pid][0]
    return f"未知雷蛇设备 1532:{pid:04X}"


# ---------------------------------------------------------------------------
# 一、PA（BlackShark V2 Pro 2023）
# ---------------------------------------------------------------------------
PA_REPORT_ID = 0x02


def pa_frame_query(cmd: int) -> bytes:
    b = bytearray(REPORT_LEN)
    b[0], b[1], b[2] = PA_REPORT_ID, 0x80, 0x08
    b[5], b[6], b[7] = 0x50, 0x41, 0x08
    b[9], b[10] = 0x03, cmd
    return bytes(b)


def pa_frame_remote(on: bool) -> bytes:
    b = bytearray(REPORT_LEN)
    b[0], b[1], b[2] = PA_REPORT_ID, 0x80, 0x07
    b[5], b[6], b[7] = 0x50, 0x41, 0x0E
    b[9], b[10], b[11] = 0x02, CMD_REMOTE, 1 if on else 0
    return bytes(b)


def pa_parse_reply(data, cmd: int) -> Optional[list]:
    """是这条命令的 ACK 就返回数据部分。也接受没有 Report ID 前缀的变体 [HALO]。"""
    d = list(data or [])
    for off in (0, -1):
        c = 12 + off
        if len(d) > c + 3 and (off == -1 or d[0] == PA_REPORT_ID):
            if d[c] == cmd and d[c + 1] == 0x01:
                n = d[c + 2]
                return d[c + 3:c + 3 + max(n, 1)]
    return None


# ---------------------------------------------------------------------------
# 二、PA 变体（Barracuda Pro）
# ---------------------------------------------------------------------------
BARRACUDA_REPORT_ID = 0x01


def barracuda_frame_query(cmd: int) -> bytes:
    b = bytearray(REPORT_LEN)
    b[0], b[1], b[2] = BARRACUDA_REPORT_ID, 0x80, 0x08
    b[3], b[4] = 0x50, 0x41
    b[5], b[6], b[7], b[8] = 0x08, 0x08, 0x03, cmd
    return bytes(b)


def barracuda_frame_remote(on: bool) -> bytes:
    b = bytearray(REPORT_LEN)
    b[0], b[1], b[2] = BARRACUDA_REPORT_ID, 0x80, 0x07
    b[3], b[4] = 0x50, 0x41
    b[5], b[6], b[7], b[8], b[9] = 0x0E, 0x08, 0x02, CMD_REMOTE, 1 if on else 0
    return bytes(b)


def barracuda_parse_reply(data, cmd: int) -> Optional[list]:
    d = list(data or [])
    for off in (0, -1):
        if off == -1 and d and d[0] == BARRACUDA_REPORT_ID:
            continue
        c = 13 + off
        if len(d) > c + 3 and d[c] == cmd and d[c + 1] in (0x01, 0x02):
            n = d[c + 2]
            if 0 < n <= 32:
                return d[c + 3:c + 3 + n]
    return None


def _pa_session(dev, output_len: int, frame_query: Callable, frame_remote: Callable,
                parse: Callable, attempts: int, diag: list) -> DeviceBattery:
    """PA 两种变体共用的流程：唤醒 → 远程模式开 → 查电量 → 查充电 → 远程模式关。"""
    res = DeviceBattery(found=True, vid=VID, diag=diag)

    def write(frame) -> bool:
        try:
            hc.write_report(dev, frame, output_len)
            return True
        except OSError as e:
            diag.append(f"  写入失败：{e}")
            return False

    def query(cmd: int) -> Optional[list]:
        for attempt in range(attempts):
            try:
                dev.drain()
            except OSError:
                pass
            write(frame_remote(True))
            time.sleep(0.035)
            if not write(frame_query(cmd)):
                return None
            end = time.monotonic() + 0.15
            while time.monotonic() < end:
                raw = dev.read_input(timeout_ms=50)
                if not raw:
                    continue
                payload = parse(raw, cmd)
                if payload is not None:
                    diag.append(f"  cmd {cmd:02x} 第 {attempt + 1} 次: {hc.hexdump(raw, 20)}")
                    return payload
            diag.append(f"  cmd {cmd:02x} 第 {attempt + 1} 次: 无回复")
        return None

    # 唤醒：接收器可能处于 USB 选择性挂起，最多试 4 次
    woke = False
    for attempt in range(4):
        if write(frame_remote(True)):
            woke = True
            break
        time.sleep(0.15 * (attempt + 1))
    if not woke:
        res.detail = "接收器不接受命令（耳机关机或 USB 休眠）"
        return res
    time.sleep(0.035)
    bat = query(CMD_BATTERY)
    if not bat:
        write(frame_remote(False))
        res.detail = "耳机未开机 / 不在接收范围（接收器在线）"
        return res
    chg = query(CMD_CHARGING)
    write(frame_remote(False))
    level = bat[0]
    if level > 100:
        res.detail = f"电量值异常：{level}"
        return res
    res.online = True
    res.level = level
    res.charging = bool(chg and chg[0]) if chg is not None else None
    res.detail = "充电中" if res.charging else ""
    return res


# ---------------------------------------------------------------------------
# 三 / 四、Feature 0xFF 交换（BlackShark V2 Pro 2020、Nari）
# ---------------------------------------------------------------------------
FF_REPORT_ID = 0xFF
FF_REQUEST = bytes.fromhex("ff 0a 00 fd 04 12 f1 02 05")
FF_REPLY_PREFIX = bytes.fromhex("ff 0f 05 fe 12 04 1f 08 05")
FF_STALE_PREFIX = bytes.fromhex("ff 01 00 fe 12 04 1f 08 05")
NARI_MV_EMPTY = 3320     # [NARI] 耳机没电关机时
NARI_MV_FULL = 3552      # [NARI] 充满、拔掉线时


def ff_parse(data) -> Optional[tuple[int, int, int]]:
    """有效回复 -> (电源状态, 电压 mV, 电量字节)。"""
    d = bytes(data or b"")
    if len(d) < 15 or not d.startswith(FF_REPLY_PREFIX):
        return None
    return d[11], int.from_bytes(d[12:14], "big"), d[14]


def ff_is_stale(data) -> bool:
    return bytes(data or b"").startswith(FF_STALE_PREFIX)


def bs2020_interpret(data) -> tuple[str, Optional[int], Optional[bool]]:
    """-> ('ok'|'offline'|'none', 电量, 充电)。规则照搬 [HALO] 的真机观察。"""
    p = ff_parse(data)
    if p is None:
        return ("offline" if ff_is_stale(data) else "none"), None, None
    state, mv, raw = p
    if mv == 0:
        return "offline", None, None
    if state == 0x09:
        return "ok", None, True              # 充电中：电量字节不可靠，不显示
    charging = False if state in (0x01, 0x02, 0x03, 0x06) else None
    return "ok", (raw if raw <= 100 else None), charging


def nari_estimate(mv: int) -> int:
    pct = (mv - NARI_MV_EMPTY) * 100 / (NARI_MV_FULL - NARI_MV_EMPTY)
    return int(max(0, min(100, round(pct))))


def _ff_exchange(dev, feature_len: int, diag: list) -> Optional[bytes]:
    n = feature_len or REPORT_LEN
    dev.set_feature_report(hc.pad(FF_REQUEST, n))
    end = time.monotonic() + 0.5
    data = None
    while True:
        time.sleep(0.1)                      # Synapse 在 SET 和 GET 之间等 ~100ms
        data = dev.get_feature_report(FF_REPORT_ID, n)
        if ff_parse(data) is not None or time.monotonic() >= end:
            break
    diag.append(f"  feature ff: {hc.hexdump(data, 16)}")
    return data


# ---------------------------------------------------------------------------
# 五、HyperSpeed（MediaTek）
# ---------------------------------------------------------------------------
HS_REPORT_ID = 0x02


def hs_build(cmd: int, domain: int, seq: int) -> bytes:
    """返回完整的 64 字节报告（[0] = Report ID 0x02）。"""
    p = bytearray(63)
    p[1] = 0x60 | (seq & 0x1F)
    p[5] = 0x04
    p[8] = domain & 0xFF
    p[9] = cmd & 0xFF
    x = HS_REPORT_ID
    for i in range(61):
        x ^= p[i]
    p[61] = x
    return bytes([HS_REPORT_ID]) + bytes(p)


def hs_parse(raw, seq: int, cmd: int) -> Optional[int]:
    """raw 为 Windows 读到的报告（[0]=0x02）。匹配 seq + 命令且校验通过时返回 p[12]。"""
    d = list(raw or [])
    if len(d) < 64 or d[0] != HS_REPORT_ID:
        return None
    p = d[1:64]
    x = 0
    for i in range(61):
        x ^= p[i]
    # [BSHS] 收到的帧按 p[0..60] 异或校验；发出的帧额外异或了 Report ID。
    # 两种都接受，再用 seq + 命令配对兜底。
    if p[61] not in (x, x ^ HS_REPORT_ID):
        return None
    if p[1] != (0x60 | (seq & 0x1F)) or p[9] != cmd:
        return None
    return p[12]


def _hs_session(dev, output_len: int, domain: int, diag: list) -> DeviceBattery:
    res = DeviceBattery(found=True, vid=VID, diag=diag)
    seq = [int(time.monotonic() * 10) & 0x1F]

    def ask(cmd: int) -> Optional[int]:
        s = seq[0]
        seq[0] = (s + 1) & 0x1F
        try:
            dev.drain()
        except OSError:
            pass
        hc.write_report(dev, hs_build(cmd, domain, s), output_len)
        end = time.monotonic() + 0.8
        while time.monotonic() < end:
            raw = dev.read_input(timeout_ms=100)
            v = hs_parse(raw, s, cmd) if raw else None
            if v is not None:
                diag.append(f"  cmd {cmd:02x} -> {v}")
                return v
        diag.append(f"  cmd {cmd:02x} -> 无回复")
        return None

    link = ask(CMD_LINK)
    if link is None:
        res.detail = "接收器没有回复（耳机关机或 USB 休眠）"
        return res
    if domain == 0x80 and link == 0:
        res.detail = "耳机未开机 / 不在接收范围（接收器在线）"
        return res
    bat = ask(CMD_BATTERY)
    if bat is None or bat > 100:
        res.detail = "耳机没有回复电量" if bat is None else f"电量值异常：{bat}"
        return res
    chg = ask(CMD_CHARGING)
    res.online = True
    res.level = bat
    res.charging = None if chg is None else chg > 0
    res.detail = "充电中" if res.charging else ""
    return res


# ---------------------------------------------------------------------------
# 接口挑选 + 入口
# ---------------------------------------------------------------------------
def _open(iface):
    from hid_win import HidDevice
    dev = HidDevice(iface.path, write=True)
    if not dev.write_access:
        dev.close()
        raise OSError("接口只能只读打开（可能被 Synapse 或其它程序独占）")
    return dev


def candidates(pid: int, ifaces: list) -> list:
    if pid in PA_2023_PIDS:
        return sorted([i for i in ifaces if i.usage_page in (0xFF00, 0xFF14) and i.output_len],
                      key=lambda i: i.usage_page != 0xFF00)
    if pid in BARRACUDA_PRO_PIDS:
        return [i for i in ifaces if i.usage_page == 0xFF00 and i.output_len]
    if pid in BS2020_PIDS:
        return [i for i in ifaces if i.usage_page == 0xFF00 and i.usage == 0x0001
                and i.feature_len]
    if pid in NARI_PIDS:
        good = [i for i in ifaces if i.feature_len and i.usage_page >= 0xFF00]
        return sorted(good, key=lambda i: hc.interface_number(i.path) != 5)
    if pid in HYPERSPEED_PIDS:
        return sorted([i for i in ifaces if i.usage_page in (0xFF14, 0xFF00) and i.output_len],
                      key=lambda i: i.usage_page != 0xFF14)
    return []


def read_device(pid: int, ifaces: list) -> DeviceBattery:
    model = model_name(pid)
    diag: list = [f"[Razer] 1532:{pid:04X} {model}"]
    base = DeviceBattery(found=True, vid=VID, pid=pid, model=model, diag=diag)
    if pid not in SUPPORTED_PIDS:
        base.detail = "这个型号没有公开的电量协议，暂不支持"
        return base
    cands = candidates(pid, ifaces)
    if not cands:
        base.detail = "没找到对应的厂商控制接口"
        return base
    last: Optional[DeviceBattery] = None
    for iface in cands:
        diag.append(f"  接口 {iface.describe_usage()} mi={hc.interface_number(iface.path)}")
        try:
            with _open(iface) as dev:
                if pid in PA_2023_PIDS:
                    res = _pa_session(dev, iface.output_len, pa_frame_query, pa_frame_remote,
                                      pa_parse_reply, 3, diag)
                elif pid in BARRACUDA_PRO_PIDS:
                    res = _pa_session(dev, iface.output_len, barracuda_frame_query,
                                      barracuda_frame_remote, barracuda_parse_reply, 4, diag)
                elif pid in HYPERSPEED_PIDS:
                    res = _hs_session(dev, iface.output_len, HYPERSPEED_PIDS[pid][1], diag)
                else:
                    data = _ff_exchange(dev, iface.feature_len, diag)
                    res = DeviceBattery(found=True, vid=VID, diag=diag)
                    if pid in BS2020_PIDS:
                        st, level, chg = bs2020_interpret(data)
                        if st == "ok":
                            res.online, res.level, res.charging = True, level, chg
                            res.approx = True
                            p = ff_parse(data)
                            res.voltage_mv = p[1] if p else None
                            res.detail = ("充电中（充电时设备报的电量不可靠，暂不显示）"
                                          if chg else "")
                        elif st == "offline":
                            res.detail = "耳机未开机 / 不在接收范围（接收器在线）"
                        else:
                            res.detail = "没有收到有效回复"
                    else:   # Nari
                        p = ff_parse(data)
                        if p is None or p[1] == 0:
                            res.detail = "耳机未开机 / 不在接收范围（接收器在线）"
                        else:
                            res.online = True
                            res.voltage_mv = p[1]
                            res.level = nari_estimate(p[1])
                            res.approx = True
                            res.detail = f"电压 {p[1]} mV（百分比为按电压估算）"
        except OSError as e:
            diag.append(f"  失败：{e}")
            last = DeviceBattery(found=True, vid=VID, diag=diag,
                                 detail=f"打开/收发失败：{e}（关掉 Synapse 再试）")
            continue
        res.pid, res.model = pid, model
        if res.online or last is None:
            last = res
        if res.online:
            return res
    last.pid, last.model = pid, model
    return last


def read_battery(all_ifaces: Optional[list] = None) -> DeviceBattery:
    if all_ifaces is None:
        from hid_win import enumerate_hid
        all_ifaces = enumerate_hid()
    mine = [i for i in all_ifaces if i.vid == VID]
    if not mine:
        return DeviceBattery(found=False, vid=VID, detail="没找到雷蛇接收器（VID 1532）")
    supported = sorted({i.pid for i in mine} & SUPPORTED_PIDS)
    if not supported:
        pids = ", ".join(f"{p:04X}" for p in sorted({i.pid for i in mine}))
        return DeviceBattery(found=True, vid=VID, pid=min(i.pid for i in mine),
                             model=model_name(min(i.pid for i in mine)),
                             detail=f"找到雷蛇设备（PID {pids}），但不是已支持的耳机型号")
    best = None
    for pid in supported:
        res = read_device(pid, [i for i in mine if i.pid == pid])
        if res.ok:
            return res
        best = best or res
    return best


if __name__ == "__main__":
    r = read_battery()
    print(r.model, r.level, "充电" if r.charging else "", r.detail)
    print("\n".join(r.diag))
