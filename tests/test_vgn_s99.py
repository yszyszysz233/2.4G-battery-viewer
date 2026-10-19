import itertools
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import device_service as ds
import vgn_s99 as s99


CAPTURE = bytes.fromhex("04 00 00 1A 06 00 00 00 33 00").ljust(64, b"\0")


def interface(**changes):
    values = dict(vid=0x320F, pid=0x5088, usage_page=0xFF1C, usage=0x92,
                  product="VGN S99 2.4G Dongle", input_len=64, output_len=64,
                  path="test-s99", manufacturer="Telink", is_vendor_page=True)
    values.update(changes)
    return SimpleNamespace(**values)


class S99Tests(unittest.TestCase):
    def test_real_keyboard_capture(self):
        result = s99.parse_reply(CAPTURE)
        self.assertEqual((result.level, result.status), (51, "discharging"))
        self.assertEqual(result.raw, CAPTURE)

    def test_empty_full_charging_and_unknown_status_are_not_guessed(self):
        for level, charge, status in ((0, 0, "discharging"), (100, 1, "charging"), (51, 2, "unknown")):
            reply = CAPTURE[:8] + bytes([level, charge]) + CAPTURE[10:]
            result = s99.parse_reply(reply)
            self.assertTrue(result.ok)
            self.assertEqual((result.level, result.status), (level, status))

    def test_unrelated_and_truncated_reports_are_ignored(self):
        for reply in (CAPTURE[:9], b"\x05" + CAPTURE[1:], CAPTURE[:3] + b"\x19" + CAPTURE[4:],
                      CAPTURE[:4] + b"\x07" + CAPTURE[5:]):
            self.assertIsNone(s99.parse_reply(reply))
        with self.assertRaises(ValueError):
            s99.parse_reply(CAPTURE[:8] + b"\xff" + CAPTURE[9:])

    def test_shared_vid_pid_and_standard_keyboard_interfaces_are_rejected(self):
        self.assertFalse(s99.is_s99(interface(product="ATK V75X 2.4G Dongle")))
        self.assertFalse(s99.is_s99(interface(usage_page=1, usage=6)))
        self.assertFalse(s99.is_s99(interface(pid=0x5055)))  # Wired mode is not verified.

    def test_only_verified_interface_receives_one_read_query(self):
        device = Mock()
        device.read_input.side_effect = [b"\x05\x00\x00", CAPTURE]
        with patch.object(s99, "device_lock", return_value=nullcontext()), \
                patch.object(s99.hw, "HidDevice") as open_device:
            open_device.return_value.__enter__.return_value = device
            result = s99.read_battery([interface(), interface(usage_page=1, usage=6, path="keyboard")])
        self.assertEqual(result.level, 51)
        open_device.assert_called_once_with("test-s99", write=True)
        device.drain.assert_called_once()
        device.write_output.assert_called_once_with(s99.QUERY.ljust(64, b"\0"), use_hidd=False)
        device.set_feature_report.assert_not_called()

    def test_sleep_timeout_is_bounded_and_does_not_report_zero_percent(self):
        device = Mock()
        device.read_input.return_value = None
        with patch.object(s99, "device_lock", return_value=nullcontext()), \
                patch.object(s99.hw, "HidDevice") as open_device, \
                patch.object(s99.time, "monotonic", side_effect=itertools.count(0, 0.3)):
            open_device.return_value.__enter__.return_value = device
            result = s99.read_battery([interface()])
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        self.assertIn("唤醒", result.detail)
        self.assertEqual(device.write_output.call_count, 3)

    def test_missing_or_ambiguous_receivers_never_receive_commands(self):
        for interfaces in ([], [interface(), interface(path="second")], [interface(product="ATK V75X")]):
            with patch.object(s99.hw, "HidDevice") as open_device:
                result = s99.read_battery(interfaces)
            self.assertFalse(result.ok)
            open_device.assert_not_called()

    def test_catalog_distinguishes_same_id_other_keyboard(self):
        with patch.object(ds.hw, "enumerate_hid", return_value=[interface()]):
            device = ds.discover_devices()["320f:5088"]
        self.assertEqual(device.provider, "vgn_s99")
        with patch.object(ds.hw, "enumerate_hid", return_value=[interface(product="ATK V75X")]):
            device = ds.discover_devices()["320f:5088"]
        self.assertFalse(device.supported)
        self.assertEqual(device.title, "ATK V75X")


if __name__ == "__main__":
    unittest.main()
