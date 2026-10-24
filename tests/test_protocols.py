# -*- coding: utf-8 -*-
"""协议解析 / 收发流程的单元测试（不需要真机，也不需要 Windows）。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_hid  # noqa: E402

hw = fake_hid.install()

import hid_common as hc  # noqa: E402
import razer_proto as rz  # noqa: E402
import steelseries_proto as ss  # noqa: E402
import xiaomi_proto as xm  # noqa: E402
import devices  # noqa: E402
from fake_hid import FakeDevice, FakeIface, path  # noqa: E402

ss.TIMEOUT = 0.2


def raw0(*data) -> bytes:
    """Windows 读到的报文：Report ID 0x00 + 数据，补到 65 字节。"""
    return bytes([0x00, *data]) + bytes(64 - len(data))


class Base(unittest.TestCase):
    def setUp(self):
        FakeDevice.reset()
        hw.IFACES = []


# ---------------------------------------------------------------------------
class TestCommon(Base):
    def test_interface_number(self):
        self.assertEqual(hc.interface_number(path(0x1038, 0x12E0, 4)), 4)
        self.assertEqual(hc.interface_number(r"\\?\HID#VID_1532&PID_0555&MI_03#x"), 3)
        self.assertIsNone(hc.interface_number(path(0x2717, 0x5091)))

    def test_hidapi_strip(self):
        self.assertEqual(hc.hidapi_strip(b"\x00\xb0\x03"), [0xB0, 0x03])
        self.assertEqual(hc.hidapi_strip(b"\x02\x01"), [0x02, 0x01])
        self.assertEqual(hc.hidapi_strip(None), [])

    def test_pad(self):
        self.assertEqual(hc.pad(b"\x00\xb0", 4), b"\x00\xb0\x00\x00")
        self.assertEqual(len(hc.pad(b"\x01" * 70, 64)), 64)

    def test_transact_skips_unrelated(self):
        dev = FakeDevice("x", write=True)
        FakeDevice.responder = staticmethod(lambda f: [raw0(0x11, 0x22), raw0(0xB0, 3, 50, 3)])
        r, first = hc.transact(dev, b"\x00\xb0", 64, lambda r: r[0] == 0xB0, timeout=0.2)
        self.assertEqual(r[:4], [0xB0, 3, 50, 3])
        self.assertEqual(first[:2], [0x11, 0x22])
        self.assertEqual(len(FakeDevice.writes[0]), 64)       # 按 OutputReportByteLength 补齐

    def test_transact_timeout(self):
        dev = FakeDevice("x", write=True)
        r, first = hc.transact(dev, b"\x00\xb0", 64, lambda r: True, timeout=0.05)
        self.assertIsNone(r)
        self.assertIsNone(first)


# ---------------------------------------------------------------------------
class TestSteelSeriesParsers(Base):
    def test_nova7(self):
        self.assertEqual(ss.parse_nova7([0xB0, 0x03, 90, 0x03]), (90, False, True))
        self.assertEqual(ss.parse_nova7([0xB0, 0x03, 40, 0x01]), (40, True, True))
        self.assertEqual(ss.parse_nova7([0xB0, 0x03, 40, 0x02]), (40, True, True))
        # 关机：link 字节不是 03 / 状态 00 —— dongle 会重复旧电量，不能当真 [HALO]
        self.assertEqual(ss.parse_nova7([0xB0, 0x00, 90, 0x00]), ss.OFF)
        self.assertEqual(ss.parse_nova7([0xB0, 0x03, 90, 0x00]), ss.OFF)
        # 不是 b0 回复
        self.assertEqual(ss.parse_nova7([0x07, 0x03, 90, 0x03]), ss.OFF)
        self.assertEqual(ss.parse_nova7([]), ss.OFF)

    def test_nova7_discrete(self):
        self.assertEqual(ss.parse_nova7_discrete([0xB0, 0x03, 3, 0x03]), (75, False, True))
        self.assertEqual(ss.parse_nova7_discrete([0xB0, 0x03, 9, 0x03])[0], 100)

    def test_nova5(self):
        self.assertEqual(ss.parse_nova5([0xB0, 0x00, 0, 77, 0x01]), (77, True, True))
        self.assertEqual(ss.parse_nova5([0xB0, 0x00, 0, 77, 0x00]), (77, False, True))
        self.assertEqual(ss.parse_nova5([0xB0, 0x02, 0, 77, 0x00]), ss.OFF)

    def test_arctis7_plus(self):
        self.assertEqual(ss.parse_arctis7_plus([0xB0, 0x03, 2, 0x01]), (50, True, True))
        self.assertEqual(ss.parse_arctis7_plus([0xB0, 0x01, 2, 0x00]), ss.OFF)

    def test_gamebuds(self):
        self.assertEqual(ss.parse_gamebuds([0xB0, 0, 0, 3, 3, 80, 60]), (60, None, True))
        self.assertEqual(ss.parse_gamebuds([0xB0, 0, 0, 3, 1, 80, 60]), (80, None, True))
        self.assertEqual(ss.parse_gamebuds([0xB0, 0, 0, 1, 1, 80, 60]), ss.OFF)

    def test_nova_pro(self):
        r = [0] * 16
        r[0], r[1], r[6], r[15] = 0x06, 0xB0, 6, 0x08
        self.assertEqual(ss.parse_nova_pro(r), (75, False, True))
        r[15] = 0x02
        self.assertEqual(ss.parse_nova_pro(r), (75, True, True))
        r[15] = 0x01
        self.assertEqual(ss.parse_nova_pro(r), ss.OFF)
        r[15], r[6] = 0x08, 9
        self.assertEqual(ss.parse_nova_pro(r), ss.OFF)

    def test_classic_exchanges(self):
        def asker(replies):
            sent = []

            def ask(req, accept):
                sent.append(bytes(req))
                rep = replies.get(bytes(req))
                return rep if rep is not None and accept(rep) else None
            return ask, sent

        ask, _ = asker({b"\x06\x12": [0x06, 0x12, 0x00, 64]})
        self.assertEqual(ss.exchange_arctis1(ask), (64, None, True))
        ask, _ = asker({b"\x06\x12": [0x06, 0x12, 0x01, 64]})
        self.assertEqual(ss.exchange_arctis1(ask), ss.OFF)

        ask, sent = asker({b"\x06\x14": [0x06, 0x14, 0x03, 0], b"\x06\x18": [0x06, 0x18, 55, 0]})
        self.assertEqual(ss.exchange_arctis7_2018(ask), (55, None, True))
        self.assertEqual(sent, [b"\x06\x14", b"\x06\x18"])
        ask, sent = asker({b"\x06\x14": [0x06, 0x14, 0x01, 0]})
        self.assertEqual(ss.exchange_arctis7_2018(ask), ss.OFF)
        self.assertEqual(sent, [b"\x06\x14"])

        ask, _ = asker({b"\x06\x18": [0x06, 0x18, 0, 0]})
        self.assertEqual(ss.exchange_arctis7(ask), ss.OFF)

        ask, _ = asker({b"\x00\x20": [0xAA, 0x01, 0, 0x9A, 0x01]})
        self.assertEqual(ss.exchange_arctis9(ask), (100, True, True))
        ask, _ = asker({b"\x00\x20": [0xAA, 0x01, 0, 0x64, 0x00]})
        self.assertEqual(ss.exchange_arctis9(ask), (0, False, True))

        ask, _ = asker({b"\x41\xaa": [0x04], b"\x40\xaa": [0x03]})
        self.assertEqual(ss.exchange_pro_wireless(ask), (75, None, True))
        ask, _ = asker({b"\x41\xaa": [0x02]})
        self.assertEqual(ss.exchange_pro_wireless(ask), ss.OFF)

    def test_pick_interfaces(self):
        ifs = [FakeIface(path(0x1038, 0x22A1, 0), 0x1038, 0x22A1, 0x000C, 1),
               FakeIface(path(0x1038, 0x22A1, 5), 0x1038, 0x22A1, 0xFFC0, 1),
               FakeIface(path(0x1038, 0x22A1, 3), 0x1038, 0x22A1, 0xFFC0, 1)]
        self.assertEqual(hc.interface_number(ss.pick_b0_interface(ifs).path), 3)
        pro = [FakeIface(path(0x1038, 0x12E0, 3), 0x1038, 0x12E0, 0xFF00, 1),
               FakeIface(path(0x1038, 0x12E0, 4), 0x1038, 0x12E0, 0xFF00, 1),
               FakeIface(path(0x1038, 0x12E0, 4), 0x1038, 0x12E0, 0x000C, 1)]
        got = [hc.interface_number(i.path) for i in ss.classic_candidates(pro, 0x12E0)]
        self.assertEqual(got, [4, 3])


class TestSteelSeriesDevice(Base):
    def nova7(self):
        return [FakeIface(path(0x1038, 0x22A1, 3), 0x1038, 0x22A1, 0xFFC0, 0x0001)]

    def test_not_found(self):
        r = ss.read_battery([])
        self.assertFalse(r.found)
        self.assertIn("没找到赛睿", r.detail)

    def test_nova7_online(self):
        FakeDevice.responder = staticmethod(
            lambda f: [raw0(0xB0, 0x03, 85, 0x03)] if f[:2] == b"\x00\xb0" else [])
        r = ss.read_battery(self.nova7())
        self.assertTrue(r.ok)
        self.assertEqual((r.level, r.charging, r.model), (85, False, "Arctis Nova 7"))
        self.assertEqual(FakeDevice.writes[0][:2], b"\x00\xb0")

    def test_nova7_off(self):
        FakeDevice.responder = staticmethod(lambda f: [raw0(0xB0, 0x00, 85, 0x00)])
        r = ss.read_battery(self.nova7())
        self.assertTrue(r.found)
        self.assertFalse(r.ok)
        self.assertIn("未开机", r.detail)

    def test_no_reply(self):
        r = ss.read_battery(self.nova7())
        self.assertFalse(r.ok)
        self.assertIn("没有回复", r.detail)

    def test_read_only(self):
        FakeDevice.write_ok = False
        r = ss.read_battery(self.nova7())
        self.assertFalse(r.ok)
        self.assertIn("只读", r.detail)

    def test_nova_pro_device(self):
        def resp(f):
            # 06 b0 里的 06 是 Report ID（hidapi 约定），回复也带 Report ID 06
            if f[:2] == b"\x06\xb0":
                r = [0] * 64
                r[0], r[1], r[6], r[15] = 0x06, 0xB0, 8, 0x02
                return [bytes(r)]
            return []
        FakeDevice.responder = staticmethod(resp)
        ifs = [FakeIface(path(0x1038, 0x12E0, 4), 0x1038, 0x12E0, 0xFF00, 1)]
        r = ss.read_battery(ifs)
        self.assertEqual((r.level, r.charging, r.approx), (100, True, True))

    def test_unknown_model(self):
        r = ss.read_battery([FakeIface(path(0x1038, 0x1234, 3), 0x1038, 0x1234)])
        self.assertTrue(r.found)
        self.assertIn("没有公开的电量协议", r.detail)


# ---------------------------------------------------------------------------
HALO_BS2020_BATTERY = bytes.fromhex("ff 0f 05 fe 12 04 1f 08 05 03 05 01 0e c0 50")
HALO_BS2020_CHARGING = bytes.fromhex("ff 0f 05 fe 12 04 1f 08 05 05 03 09 10 80 50")
HALO_BS2020_FULL = bytes.fromhex("ff 0f 05 fe 12 04 1f 08 05 06 05 06 10 88 64")
HALO_BS2020_STALE = bytes.fromhex("ff 01 00 fe 12 04 1f 08 05") + bytes(6)


class TestRazerParsers(Base):
    def test_pa_frames(self):
        q = rz.pa_frame_query(0x21)
        self.assertEqual(len(q), 64)
        self.assertEqual(q[:11], bytes([0x02, 0x80, 0x08, 0, 0, 0x50, 0x41, 0x08, 0, 0x03, 0x21]))
        on = rz.pa_frame_remote(True)
        self.assertEqual(on[:12], bytes([0x02, 0x80, 0x07, 0, 0, 0x50, 0x41, 0x0E, 0, 0x02, 0xE1, 1]))
        self.assertEqual(rz.pa_frame_remote(False)[11], 0)

    def test_pa_parse(self):
        reply = bytearray(64)
        reply[0], reply[12], reply[13], reply[14], reply[15] = 0x02, 0x21, 0x01, 0x01, 85
        self.assertEqual(rz.pa_parse_reply(bytes(reply), 0x21), [85])
        self.assertIsNone(rz.pa_parse_reply(bytes(reply), 0x2A))
        reply[13] = 0x00                      # 不是 ACK
        self.assertIsNone(rz.pa_parse_reply(bytes(reply), 0x21))

    def test_barracuda(self):
        q = rz.barracuda_frame_query(0x21)
        self.assertEqual(q[:9], bytes([0x01, 0x80, 0x08, 0x50, 0x41, 0x08, 0x08, 0x03, 0x21]))
        reply = bytearray(64)
        reply[0], reply[3], reply[4] = 0x01, 0x50, 0x49
        reply[13], reply[14], reply[15], reply[16] = 0x21, 0x02, 0x01, 42
        self.assertEqual(rz.barracuda_parse_reply(bytes(reply), 0x21), [42])
        reply[15] = 0
        self.assertIsNone(rz.barracuda_parse_reply(bytes(reply), 0x21))

    def test_bs2020_halo_samples(self):
        self.assertEqual(rz.ff_parse(HALO_BS2020_BATTERY), (0x01, 0x0EC0, 80))
        self.assertEqual(rz.bs2020_interpret(HALO_BS2020_BATTERY), ("ok", 80, False))
        self.assertEqual(rz.bs2020_interpret(HALO_BS2020_CHARGING), ("ok", None, True))
        self.assertEqual(rz.bs2020_interpret(HALO_BS2020_FULL), ("ok", 100, False))
        self.assertEqual(rz.bs2020_interpret(HALO_BS2020_STALE), ("offline", None, None))
        zero_mv = bytearray(HALO_BS2020_BATTERY)
        zero_mv[12] = zero_mv[13] = 0
        self.assertEqual(rz.bs2020_interpret(bytes(zero_mv))[0], "offline")
        self.assertEqual(rz.bs2020_interpret(b"")[0], "none")

    def test_nari_estimate(self):
        self.assertEqual(rz.nari_estimate(3320), 0)
        self.assertEqual(rz.nari_estimate(3552), 100)
        self.assertEqual(rz.nari_estimate(3436), 50)
        self.assertEqual(rz.nari_estimate(4000), 100)
        self.assertEqual(rz.nari_estimate(3000), 0)

    def test_hs_build(self):
        f = rz.hs_build(0x21, 0x80, 3)
        self.assertEqual(len(f), 64)
        self.assertEqual(f[0], 0x02)
        p = f[1:]
        self.assertEqual((p[1], p[5], p[8], p[9]), (0x63, 0x04, 0x80, 0x21))
        x = 0x02
        for b in p[:61]:
            x ^= b
        self.assertEqual(p[61], x)

    def test_hs_parse(self):
        p = bytearray(63)
        p[1], p[5], p[8], p[9], p[12] = 0x63, 0x04, 0x80, 0x21, 77
        x = 0
        for b in p[:61]:
            x ^= b
        p[61] = x
        raw = bytes([0x02]) + bytes(p)
        self.assertEqual(rz.hs_parse(raw, 3, 0x21), 77)
        self.assertIsNone(rz.hs_parse(raw, 4, 0x21))       # seq 不对
        self.assertIsNone(rz.hs_parse(raw, 3, 0x2A))       # 命令不对
        bad = bytearray(raw)
        bad[62] ^= 0x55
        self.assertIsNone(rz.hs_parse(bytes(bad), 3, 0x21))
        p[61] = x ^ 0x02                                    # 带 Report ID 的校验也接受
        self.assertEqual(rz.hs_parse(bytes([0x02]) + bytes(p), 3, 0x21), 77)


class TestRazerDevice(Base):
    def test_not_found(self):
        r = rz.read_battery([])
        self.assertFalse(r.found)

    def test_unsupported(self):
        r = rz.read_battery([FakeIface(path(0x1532, 0x0099, 0), 0x1532, 0x0099)])
        self.assertTrue(r.found)
        self.assertFalse(r.ok)
        self.assertIn("不是已支持", r.detail)

    def test_bs2020_device(self):
        FakeDevice.feature_responder = staticmethod(
            lambda f: HALO_BS2020_BATTERY + bytes(49) if f.startswith(rz.FF_REQUEST) else b"")
        ifs = [FakeIface(path(0x1532, 0x0528, 3), 0x1532, 0x0528, 0xFF00, 1, feature_len=64)]
        r = rz.read_battery(ifs)
        self.assertEqual((r.level, r.charging, r.voltage_mv, r.approx), (80, False, 3776, True))
        self.assertEqual(len(FakeDevice.writes[0]), 64)

    def test_bs2020_off(self):
        FakeDevice.feature_responder = staticmethod(lambda f: HALO_BS2020_STALE)
        ifs = [FakeIface(path(0x1532, 0x0528, 3), 0x1532, 0x0528, 0xFF00, 1, feature_len=64)]
        r = rz.read_battery(ifs)
        self.assertFalse(r.ok)
        self.assertIn("未开机", r.detail)

    def test_nari_device(self):
        reply = bytearray(HALO_BS2020_BATTERY)
        reply[12:14] = (3436).to_bytes(2, "big")
        FakeDevice.feature_responder = staticmethod(lambda f: bytes(reply))
        ifs = [FakeIface(path(0x1532, 0x051C, 5), 0x1532, 0x051C, 0xFF00, 1, feature_len=64)]
        r = rz.read_battery(ifs)
        self.assertEqual((r.level, r.voltage_mv, r.approx), (50, 3436, True))

    def test_pa_device(self):
        def resp(f):
            if f[9] == 0x03:                  # 查询帧
                reply = bytearray(64)
                reply[0], reply[12], reply[13], reply[14] = 0x02, f[10], 0x01, 0x01
                reply[15] = 64 if f[10] == 0x21 else 1
                return [bytes(reply)]
            return []
        FakeDevice.responder = staticmethod(resp)
        ifs = [FakeIface(path(0x1532, 0x0555, 3), 0x1532, 0x0555, 0xFF00, 1)]
        r = rz.read_battery(ifs)
        self.assertEqual((r.level, r.charging, r.model), (64, True, "Razer BlackShark V2 Pro (2023)"))
        self.assertEqual(FakeDevice.writes[-1][11], 0)       # 最后关掉远程模式

    def test_pa_device_off(self):
        ifs = [FakeIface(path(0x1532, 0x0555, 3), 0x1532, 0x0555, 0xFF00, 1)]
        r = rz.read_battery(ifs)
        self.assertTrue(r.found)
        self.assertFalse(r.ok)
        self.assertIn("未开机", r.detail)

    def _hs_reply(self, f, value):
        p = bytearray(63)
        p[1], p[8], p[9], p[12] = f[2], f[9], f[10], value
        x = 0
        for b in p[:61]:
            x ^= b
        p[61] = x
        return bytes([0x02]) + bytes(p)

    def test_hyperspeed(self):
        vals = {0x20: 1, 0x21: 93, 0x2A: 0}
        FakeDevice.responder = staticmethod(lambda f: [self._hs_reply(f, vals[f[10]])])
        ifs = [FakeIface(path(0x1532, 0x0565, 3), 0x1532, 0x0565, 0xFF14, 1)]
        r = rz.read_battery(ifs)
        self.assertEqual((r.level, r.charging), (93, False))

    def test_hyperspeed_headset_off(self):
        vals = {0x20: 0, 0x21: 93, 0x2A: 0}
        FakeDevice.responder = staticmethod(lambda f: [self._hs_reply(f, vals[f[10]])])
        ifs = [FakeIface(path(0x1532, 0x0565, 3), 0x1532, 0x0565, 0xFF14, 1)]
        r = rz.read_battery(ifs)
        self.assertFalse(r.ok)
        self.assertIn("未开机", r.detail)


# ---------------------------------------------------------------------------
class TestXiaomi(Base):
    def test_not_found(self):
        r = xm.read_battery([])
        self.assertFalse(r.found)

    def test_known_models_include_x1(self):
        self.assertEqual(xm.KNOWN_MODELS[0x50A5], "小米鼠标 X1")
        self.assertEqual(xm.KNOWN_MODELS[0x50A6], "小米鼠标 X1")

    def test_checksum65_and_request_layout(self):
        req = xm.build_battery_request(product_type=2, param=0)
        self.assertEqual(len(req), 65)
        # opcode 0x8108 LE + len=0x01 + product_type=2
        self.assertEqual(list(req[:9]), [0x00, 0x00, 0x08, 0x81, 0x01, 0x02, 0x00, 0x00, 0x00])
        s = sum(req[0:63]) & 0xFFFF
        self.assertEqual(req[63], s & 0xFF)
        self.assertEqual(req[64], (s >> 8) & 0xFF)
        self.assertEqual(req.hex(), xm.build_battery_request(2, 0).hex())

    def test_parse_battery_response(self):
        # 电量在 [7]；要求 status=01 且 opcode 回显 08 81
        ok = bytearray(65)
        ok[1], ok[2], ok[3], ok[7] = 0x01, 0x08, 0x81, 55
        self.assertEqual(xm.parse_battery_response(bytes(ok)), 55)
        nak = bytearray(ok); nak[1] = 0x03
        self.assertIsNone(xm.parse_battery_response(bytes(nak)))
        wrong_op = bytearray(ok); wrong_op[2], wrong_op[3] = 0x03, 0x81  # 旧 0x8103
        self.assertIsNone(xm.parse_battery_response(bytes(wrong_op)))
        self.assertIsNone(xm.parse_battery_response(b""))
        clamp = bytearray(65)
        clamp[1], clamp[2], clamp[3], clamp[7] = 0x01, 0x08, 0x81, 0xFF
        self.assertEqual(xm.parse_battery_response(bytes(clamp)), 100)

    def test_vendor_feature_x1_success(self):
        iface = FakeIface(path(0x2717, 0x50A5, 2), 0x2717, 0x50A5,
                          0xFFFF, 0x0001, feature_len=65)

        def responder(frame: bytes) -> bytes:
            self.assertEqual(list(frame[2:6]), [0x08, 0x81, 0x01, 0x02])
            out = bytearray(65)
            out[1], out[2], out[3], out[5], out[7] = 0x01, 0x08, 0x81, 0x02, 73
            return bytes(out)

        FakeDevice.feature_responder = staticmethod(responder)
        old_s, old_g = xm.VENDOR_SETTLE_S, xm.VENDOR_GET_GAP_S
        xm.VENDOR_SETTLE_S = xm.VENDOR_GET_GAP_S = 0
        try:
            r = xm.read_battery([iface], listen_seconds=0.01)
        finally:
            xm.VENDOR_SETTLE_S, xm.VENDOR_GET_GAP_S = old_s, old_g
        self.assertTrue(r.ok)
        self.assertEqual(r.level, 73)
        self.assertEqual(r.model, "小米鼠标 X1")
        self.assertIn("0x8108", r.detail)
        self.assertTrue(any("vendor 0x8108" in d for d in r.diag))

    def test_vendor_skips_ghub_pollution(self):
        """G-HUB 粘滞帧（非 08 81）应被跳过，直到读到本 opcode 回显。"""
        iface = FakeIface(path(0x2717, 0x50A5, 2), 0x2717, 0x50A5,
                          0xFFFF, 0x0001, feature_len=65)
        seen_req = []

        class SeqDev(FakeDevice):
            def __init__(self, path, write=False):
                super().__init__(path, write)
                self._gets = 0

            def set_feature_report(self, data: bytes):
                FakeDevice.writes.append(bytes(data))
                seen_req.append(list(data[2:6]))

            def get_feature_report(self, report_id: int, length=None):
                self._gets += 1
                out = bytearray(65)
                if self._gets == 1:
                    # G-HUB 8704 粘滞
                    out[1], out[2], out[3] = 0x01, 0x04, 0x87
                    out[7] = 1
                else:
                    out[1], out[2], out[3], out[7] = 0x01, 0x08, 0x81, 55
                return bytes(out)

        import hid_win as hw
        old_cls = hw.HidDevice
        hw.HidDevice = SeqDev
        old_s, old_g = xm.VENDOR_SETTLE_S, xm.VENDOR_GET_GAP_S
        xm.VENDOR_SETTLE_S = xm.VENDOR_GET_GAP_S = 0
        try:
            r = xm.read_battery([iface], listen_seconds=0.01)
        finally:
            xm.VENDOR_SETTLE_S, xm.VENDOR_GET_GAP_S = old_s, old_g
            hw.HidDevice = old_cls
        self.assertGreaterEqual(len(seen_req), 1)
        self.assertTrue(all(r == [0x08, 0x81, 0x01, 0x02] for r in seen_req))
        self.assertTrue(r.ok)
        self.assertEqual(r.level, 55)

    def test_vendor_nak_falls_through(self):
        iface = FakeIface(path(0x2717, 0x50A5, 2), 0x2717, 0x50A5,
                          0xFFFF, 0x0001, feature_len=65)
        FakeDevice.feature_responder = staticmethod(
            lambda frame: bytes([0x00, 0x03]) + bytes(63))
        old_s, old_g = xm.VENDOR_SETTLE_S, xm.VENDOR_GET_GAP_S
        xm.VENDOR_SETTLE_S = xm.VENDOR_GET_GAP_S = 0
        try:
            r = xm.read_battery([iface], listen_seconds=0.01)
        finally:
            xm.VENDOR_SETTLE_S, xm.VENDOR_GET_GAP_S = old_s, old_g
        self.assertTrue(r.found)
        self.assertFalse(r.ok)
        self.assertIn("未公开电量协议", r.detail)

    def test_no_standard_fields(self):
        ifs = [FakeIface(path(0x2717, 0x5091, 0), 0x2717, 0x5091, 0x0001, 0x0002),
               FakeIface(path(0x2717, 0x5091, 1), 0x2717, 0x5091, 0xFF00, 0x0001, feature_len=8)]
        r = xm.read_battery(ifs, listen_seconds=0.05)
        self.assertTrue(r.found)
        self.assertFalse(r.ok)
        self.assertEqual(r.model, "小米 USB 接收器（Mi USB Receiver）")
        self.assertIn("未公开电量协议", r.detail)

    def test_rf_signal_is_not_battery(self):
        self.assertNotIn((0x0006, 0x0029), [(p, u) for p, u, _ in xm.BATTERY_USAGES])


# ---------------------------------------------------------------------------
class TestDevicesLayer(Base):
    def test_reading_mapping(self):
        res = hc.DeviceBattery(found=True, online=True, level=75, charging=True, approx=True,
                               vid=0x1038, pid=0x12E0, model="Arctis Nova Pro Wireless",
                               detail="充电中")
        r = devices.to_reading("赛睿耳机", res)
        self.assertEqual((r.level_text, r.status, r.found), ("≈75%", "充电中", True))
        self.assertIn("1038:12E0", r.subtitle)

        r = devices.to_reading("雷蛇耳机", hc.DeviceBattery(found=False, detail="没找到雷蛇接收器"))
        self.assertFalse(r.ok)
        self.assertFalse(r.found)
        self.assertEqual(r.error, "没找到雷蛇接收器")

    def test_read_all_empty(self):
        rs = devices.read_all()
        self.assertEqual([r.title for r in rs], ["小米鼠标", "雷蛇耳机", "赛睿耳机"])
        self.assertTrue(all(not r.ok and not r.found for r in rs))

    def test_read_all_mixed(self):
        hw.IFACES = [FakeIface(path(0x1038, 0x22A1, 3), 0x1038, 0x22A1, 0xFFC0, 0x0001)]
        FakeDevice.responder = staticmethod(lambda f: [raw0(0xB0, 0x03, 42, 0x01)])
        rs = devices.read_all_dict()
        self.assertEqual(rs["steelseries"].level, 42)
        self.assertEqual(rs["steelseries"].status, "充电中")
        self.assertFalse(rs["razer"].found)



class TestBatteryStrategy(Base):
    """app.py 调试工具走的 battery.read_battery 也能用上新协议。"""

    def test_family_strategy(self):
        import battery as bt
        iface = FakeIface(path(0x1038, 0x22A1, 3), 0x1038, 0x22A1, 0xFFC0, 0x0001)
        hw.IFACES = [iface]
        FakeDevice.responder = staticmethod(lambda f: [raw0(0xB0, 0x03, 66, 0x03)])
        res = bt.strategy_family_protocols(iface)
        self.assertTrue(res.ok)
        self.assertEqual((res.level, res.status), (66, "discharging"))
        self.assertIsNone(bt.strategy_family_protocols(
            FakeIface(path(0x046D, 0xC52B, 2), 0x046D, 0xC52B)))


if __name__ == "__main__":
    unittest.main()
