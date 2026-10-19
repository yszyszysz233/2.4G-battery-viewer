import sys
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gm3323d_proto as gm


class BatteryReports(unittest.TestCase):
    def test_captured_wireless_report(self):
        result = gm.parse_report(bytes.fromhex("03 00 40 01 08"))
        self.assertEqual(result.level, 80)

    def test_alternate_status_report_and_full(self):
        self.assertEqual(gm.parse_report(bytes.fromhex("03 01 40 01 0a")).level, 100)

    def test_offline_does_not_display_retained_level(self):
        result = gm.parse_report(bytes.fromhex("03 00 40 00 08"))
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)

    def test_zero_and_out_of_range_are_unknown_not_clamped(self):
        for value in (0, 11, 80, 100, 255):
            with self.subTest(value=value):
                self.assertIsNone(gm.parse_report(bytes([3, 0, 0x40, 1, value])).level)

    def test_unrelated_or_malformed_packets_are_ignored(self):
        for raw in (b"", b"\x03\x00", bytes.fromhex("03 00 60 01 08"),
                    bytes.fromhex("04 00 40 01 08"), bytes.fromhex("03 00 40 01 08 00")):
            with self.subTest(raw=raw):
                self.assertIsNone(gm.parse_report(raw))

    def test_cli_reports_success_or_unknown_with_exit_status(self):
        for result, expected_code, expected_text in (
                (gm.Reading(level=80, detail="10% steps"), 0, "80%"),
                (gm.Reading(detail="timeout"), 1, "未知")):
            with self.subTest(result=result), patch.object(gm, "read_battery", return_value=result):
                with patch("sys.stdout", new_callable=io.StringIO) as output:
                    self.assertEqual(gm.main(), expected_code)
                    self.assertIn(expected_text, output.getvalue())


def interface(**changes):
    values = dict(vid=gm.VID, pid=gm.PID, usage_page=gm.USAGE_PAGE,
                  usage=0, input_len=5, path="test-interface")
    values.update(changes)
    return SimpleNamespace(**values)


class ReaderBehavior(unittest.TestCase):
    def setUp(self):
        self.device = Mock()
        self.device.__enter__ = Mock(return_value=self.device)
        self.device.__exit__ = Mock(return_value=False)
        self.device._open_reader.return_value = True
        self.hw = SimpleNamespace(enumerate_hid=Mock(return_value=[interface()]),
                                  HidDevice=Mock(return_value=self.device))
        self.patch = patch.dict(sys.modules, {"hid_win": self.hw})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_only_exact_device_and_interface_match(self):
        for changes in ({"pid": gm.WIRED_PID}, {"vid": 0x1234},
                        {"usage_page": 11}, {"usage": 1}, {"input_len": 15}):
            self.assertFalse(gm.matches(interface(**changes)))

    def test_no_device_has_clear_error_and_never_opens_anything(self):
        self.hw.enumerate_hid.return_value = []
        self.assertFalse(gm.read_battery().ok)
        self.hw.HidDevice.assert_not_called()

    def test_wired_only_is_not_presented_as_wireless_battery(self):
        self.hw.enumerate_hid.return_value = [interface(pid=gm.WIRED_PID)]
        result = gm.read_battery()
        self.assertFalse(result.ok)
        self.assertIn("有线", result.detail)
        self.hw.HidDevice.assert_not_called()

    def test_valid_read_uses_read_only_access_and_closes(self):
        self.device.read_input.side_effect = [b"\x03\x00\x60\x01\x08",
                                              b"\x03\x00\x40\x01\x08"]
        self.assertEqual(gm.read_battery().level, 80)
        self.hw.HidDevice.assert_called_once_with("test-interface", write=False)
        self.device.set_feature_report.assert_not_called()
        self.device.write_output.assert_not_called()
        self.device.__exit__.assert_called_once()

    def test_timeout_does_not_return_previous_success(self):
        self.device.read_input.side_effect = [b"\x03\x00\x40\x01\x08", None]
        self.assertEqual(gm.read_battery().level, 80)
        self.assertFalse(gm.read_battery().ok)

    def test_open_failure_does_not_spin(self):
        self.device._open_reader.return_value = False
        self.assertFalse(gm.read_battery().ok)
        self.device.read_input.assert_not_called()

    def test_unplug_during_read_returns_error(self):
        self.device.read_input.side_effect = OSError("disconnected")
        self.assertFalse(gm.read_battery().ok)
        self.device.__exit__.assert_called_once()


@unittest.skipUnless(sys.platform == "win32", "battery.py uses Windows HID APIs")
class StrategyIntegration(unittest.TestCase):
    def test_gm_timeout_does_not_trigger_other_device_probes(self):
        import battery as bt
        with patch.object(gm, "read_battery", return_value=gm.Reading(detail="timeout")):
            with patch.object(bt, "strategy_standard_battery_usage") as standard:
                with patch.object(bt, "strategy_vendor_protocol") as vendor:
                    results = bt.read_battery(interface())
                    self.assertEqual(len(results), 1)
                    self.assertFalse(results[0].ok)
                    standard.assert_not_called()
                    vendor.assert_not_called()

    def test_other_devices_still_use_existing_strategies(self):
        import battery as bt
        expected = bt.BatteryResult(ok=True, level=60, source="standard HID")
        with patch.object(gm, "read_battery") as reader:
            with patch.object(bt, "strategy_standard_battery_usage", return_value=expected):
                self.assertEqual(bt.read_battery(interface(vid=0x1234)), [expected])
                reader.assert_not_called()


if __name__ == "__main__":
    unittest.main()
