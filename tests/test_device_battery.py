"""Run on Windows: python -B -m unittest discover -s tests -v.

Protocol regressions use synthetic reports and never access physical devices.
"""
import unittest
from contextlib import nullcontext
from dataclasses import replace
from itertools import count
from types import SimpleNamespace
from unittest.mock import Mock, patch

import battery as bt
import darkrim_proto as drp
import device_readers as readers
import hid_win as hw


def interface(vid=0x1532, pid=0x00A6, **kwargs):
    data = dict(path="fake", vid=vid, pid=pid, version=0, usage_page=1, usage=2,
                input_len=9, output_len=0, feature_len=91,
                product="test", manufacturer="", serial="")
    data.update(kwargs)
    return hw.HidInterface(**data)


def razer_reply(command=0x80, value=161, status=2):
    report = bytearray(bt._razer_request(command))
    report[1], report[10] = status, value
    report[89] = 0
    for byte in report[3:89]:
        report[89] ^= byte
    return bytes(report)


def aula_reply(level=100, state=1):
    report = bytearray(20)
    report[:7] = bytes((0x13, 0x4A, 1, 0, 2, level, state))
    report[19] = sum(report[:19]) & 255
    return bytes(report)


class RazerTests(unittest.TestCase):
    def query(self, reports, timeout=0.5):
        dev = Mock()
        dev.get_feature_report.side_effect = reports
        with patch.object(bt.time, "sleep"):
            return bt._razer_query(dev, 0x80, timeout)

    def test_real_capture_converts_to_63_percent(self):
        dev = Mock()
        dev.__enter__ = Mock(return_value=dev)
        dev.__exit__ = Mock(return_value=False)
        with patch.object(hw, "query_lock", return_value=nullcontext()), \
                patch.object(bt, "HidDevice", return_value=dev), \
                patch.object(bt, "_razer_query", side_effect=[razer_reply(), razer_reply(0x84, 0)]):
            result = bt.strategy_razer_viper(interface())
        self.assertTrue(result.ok)
        self.assertEqual((result.level, result.raw_level, result.status), (63, 161, "discharging"))

    def test_busy_and_foreign_response_are_skipped(self):
        result = self.query([razer_reply(status=1), razer_reply(0x84), razer_reply()])
        self.assertEqual(result[10], 161)

    def test_bad_checksum_and_short_report_are_skipped(self):
        bad = bytearray(razer_reply())
        bad[10] ^= 1
        self.assertEqual(self.query([bytes(bad), b"\x00", razer_reply()])[10], 161)

    def test_only_invalid_responses_time_out(self):
        dev = Mock()
        dev.get_feature_report.return_value = razer_reply(0x84)
        with patch.object(bt.time, "sleep"), \
                patch.object(bt.time, "monotonic", side_effect=count(0, 0.1).__next__):
            with self.assertRaises(hw.HidError):
                bt._razer_query(dev, 0x80, timeout=0.5)

    def test_sleep_status_is_not_zero_percent(self):
        with self.assertRaises(hw.HidError):
            self.query([razer_reply(value=0, status=4)])

    def test_other_razer_device_is_not_probed(self):
        self.assertIsNone(bt.strategy_razer_viper(interface(pid=0x0099)))

    def test_feature_query_works_without_write_access(self):
        dev = object.__new__(hw.HidDevice)
        dev.handle, dev.write_access = 1, False
        with patch.object(hw.hid, "HidD_SetFeature", return_value=True) as send:
            dev.set_feature_report(bt._razer_request(0x80))
        send.assert_called_once()
        with self.assertRaises(hw.HidError):
            dev.write_output(b"\x00")


class AulaTests(unittest.TestCase):
    def test_real_capture_and_zero_percent(self):
        for level in (0, 100):
            with self.subTest(level=level):
                result = bt._parse_aula_battery(aula_reply(level))
                self.assertTrue(result.ok)
                self.assertEqual((result.level, result.status), (level, "discharging"))

    def test_charging_does_not_claim_100_percent(self):
        result = bt._parse_aula_battery(aula_reply(100, 0x10))
        self.assertTrue(result.ok)
        self.assertIsNone(result.level)
        self.assertEqual(result.status, "charging")

    def test_echo_corruption_invalid_state_and_range_rejected(self):
        bad = bytearray(aula_reply())
        bad[5] ^= 1
        for report in (bt._aula_request(), bytes(bad), aula_reply()[:-1],
                       aula_reply(101), aula_reply(state=0)):
            with self.subTest(report=report):
                self.assertIsNone(bt._parse_aula_battery(report))

    def test_wireless_query_skips_echo_and_uses_checked_reply(self):
        iface = interface(0x3554, 0xFA09, usage_page=0xFF02, input_len=20,
                          output_len=20, feature_len=0)
        dev = Mock()
        dev.__enter__ = Mock(return_value=dev)
        dev.__exit__ = Mock(return_value=False)
        dev.read_input.side_effect = [bt._aula_request(), aula_reply(75)]
        with patch.object(hw, "query_lock", return_value=nullcontext()), \
                patch.object(bt, "HidDevice", return_value=dev):
            result = bt.strategy_aula_f87(iface)
        self.assertEqual(result.level, 75)
        dev.write_output.assert_called_once_with(bt._aula_request())
        dev.drain.assert_called_once()

    def test_other_3554_device_is_not_probed(self):
        self.assertIsNone(bt.strategy_aula_f87(interface(0x3554, 0x1234)))

    def test_keyboard_never_receives_vxe_mouse_commands(self):
        iface = interface(0x3554, 0xFA09, usage_page=0xFF02, output_len=20)
        with patch.object(bt, "HidDevice") as device:
            self.assertIsNone(bt.strategy_vendor_protocol(iface))
        device.assert_not_called()


class ReaderTests(unittest.TestCase):
    def test_enumerates_once_and_keeps_device_errors_separate(self):
        iface = interface()
        with patch.object(hw, "enumerate_hid", return_value=[iface]) as enum, \
                patch.object(bt, "strategy_razer_viper", return_value=bt.BatteryResult(
                    ok=True, level=63, status="discharging")):
            mouse, keyboard = readers.read_all()
        enum.assert_called_once()
        self.assertEqual(mouse.level, 63)
        self.assertFalse(keyboard.ok)
        self.assertTrue(keyboard.error)

    def test_charging_and_zero_have_valid_display(self):
        charging = readers.Reading("keyboard", "", status="充电中")
        zero = readers.Reading("mouse", "", level=0)
        self.assertTrue(charging.ok)
        self.assertEqual(charging.display_level, "充电中")
        self.assertTrue(zero.ok)
        self.assertEqual(zero.display_level, "0%")

    def test_both_unplugged(self):
        with patch.object(hw, "enumerate_hid", return_value=[]):
            self.assertTrue(all(not reading.ok for reading in readers.read_all()))

    def test_original_atk_and_darkrim_remain_supported(self):
        atk = interface(0x373B, 0x1216, usage_page=0xFF05, output_len=64)
        darkrim = interface(0x3654, 0x6901, usage_page=0xFF02, output_len=65)
        with patch.object(hw, "enumerate_hid", return_value=[atk, darkrim]), \
                patch.object(bt, "strategy_vendor_protocol", return_value=bt.BatteryResult(ok=True, level=80)), \
                patch.object(bt, "strategy_darkrim", return_value=bt.BatteryResult(ok=True, level=70)):
            mouse, headset = readers.read_all()
        self.assertEqual((mouse.title, headset.title), readers.LEGACY_DEVICE_TITLES)
        self.assertEqual((mouse.level, headset.level), (80, 70))

    def test_razer_can_be_used_with_original_darkrim(self):
        darkrim = interface(0x3654, 0x6901, usage_page=0xFF02, output_len=65)
        with patch.object(hw, "enumerate_hid", return_value=[interface(), darkrim]), \
                patch.object(bt, "strategy_razer_viper", return_value=bt.BatteryResult(ok=True, level=63)), \
                patch.object(bt, "strategy_darkrim", return_value=bt.BatteryResult(ok=True, level=70)):
            mouse, headset = readers.read_all()
        self.assertEqual((mouse.title, headset.title), (readers.MOUSE_TITLE, readers.LEGACY_DEVICE_TITLES[1]))
        self.assertTrue(mouse.ok and headset.ok)

    def test_atk_can_be_used_with_aula_keyboard(self):
        atk = interface(0x373B, 0x1216, usage_page=0xFF05, output_len=64)
        aula = interface(0x3554, 0xFA09, usage_page=0xFF02, input_len=20, output_len=20, feature_len=0)
        with patch.object(hw, "enumerate_hid", return_value=[atk, aula]), \
                patch.object(bt, "strategy_vendor_protocol", return_value=bt.BatteryResult(ok=True, level=80)), \
                patch.object(bt, "strategy_aula_f87", return_value=bt.BatteryResult(ok=True, level=100)):
            mouse, keyboard = readers.read_all()
        self.assertEqual((mouse.title, keyboard.title), (readers.LEGACY_DEVICE_TITLES[0], readers.KEYBOARD_TITLE))
        self.assertTrue(mouse.ok and keyboard.ok)


class LegacyValidationTests(unittest.TestCase):
    def test_mouse_unmatched_reply_is_not_used_after_retries(self):
        dev = Mock()
        dev.read_input.return_value = bytes((8, 0, 0, 0, 0, 0, 0, 70))
        spec = replace(bt.BUILTIN_PROTOCOLS[0], retries=1, wait_ms=0)
        with patch.object(bt.time, "sleep"), \
                patch.object(bt.time, "monotonic", side_effect=count(0, 0.2).__next__):
            with self.assertRaises(hw.HidError):
                bt._read_report_after_request(dev, spec, "output")

    def test_darkrim_crc_failure_is_filtered(self):
        bad = bytearray(drp.build_frame(0, bytes((1, 1, 70))))
        bad[14] ^= 1
        dev = Mock(caps=SimpleNamespace(InputReportByteLength=65))
        dev.read_input.return_value = bytes(bad)
        with patch.object(drp.time, "monotonic", side_effect=count(0, 0.2).__next__):
            self.assertEqual(drp.query(dev, 0, 1, timeout=0.4), [])


if __name__ == "__main__":
    unittest.main()
