import itertools
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

import device_service as ds
import mchose_x9 as x9


CAPTURE = bytes.fromhex("55 65 14 02").ljust(64, b"\0")


def interface(**changes):
    values = dict(vid=0x3837, pid=0x6045, product="MCHOSE X9",
                  usage_page=0xFF90, usage=1, input_len=64, output_len=64,
                  feature_len=0, path="test-x9", manufacturer="MCHOSE", is_vendor_page=True)
    values.update(changes)
    return SimpleNamespace(**values)


class X9Tests(unittest.TestCase):
    def test_real_receiver_capture_and_device_service(self):
        result = x9.parse_reply(CAPTURE)
        self.assertEqual((result.level, result.status), (20, "discharging"))
        self.assertEqual(result.raw, CAPTURE)
        with patch.object(ds.hw, "enumerate_hid", return_value=[interface()]), \
                patch.object(x9, "read_battery", return_value=result):
            device = ds.discover_devices()["3837:6045"]
            reading = ds.read_device(device)
        self.assertEqual(device.provider, "mchose_x9")
        self.assertEqual((reading.level, reading.status, reading.state), (20, "放电中", "ready"))

    def test_percentage_limits_and_official_charge_statuses(self):
        for level, state, status in ((0, 2, "discharging"), (37, 3, "charging"),
                                     (100, 4, "full"), (20, 99, "unknown")):
            result = x9.parse_reply(CAPTURE[:2] + bytes([level, state]) + CAPTURE[4:])
            self.assertTrue(result.ok)
            self.assertEqual((result.level, result.status), (level, status))

    def test_sleep_status_never_exposes_placeholder_or_stale_percentage(self):
        for level in (0, 20, 255):
            result = x9.parse_reply(CAPTURE[:2] + bytes([level, 0x1A]) + CAPTURE[4:])
            self.assertFalse(result.ok)
            self.assertIsNone(result.level)
            self.assertIn("休眠", result.detail)

    def test_truncated_and_unrelated_reports_are_ignored(self):
        for reply in (b"", CAPTURE[:3], CAPTURE[:63], CAPTURE + b"\0",
                      b"\x41" + CAPTURE[1:], b"\x55\x22" + CAPTURE[2:]):
            self.assertIsNone(x9.parse_reply(reply))
        with self.assertRaises(ValueError):
            x9.parse_reply(CAPTURE[:2] + b"\xff" + CAPTURE[3:])

    def test_only_exact_model_and_battery_collection_receive_query(self):
        device = Mock()
        device.read_input.side_effect = [b"\x55\x22".ljust(64, b"\0"), CAPTURE]
        with patch.object(x9, "device_lock", return_value=nullcontext()), \
                patch.object(x9.hw, "HidDevice") as open_device:
            open_device.return_value.__enter__.return_value = device
            result = x9.read_battery([interface(), interface(usage_page=0xFF82, path="firmware")])
        self.assertEqual(result.level, 20)
        open_device.assert_called_once_with("test-x9", write=True)
        device.drain.assert_called_once()
        device.write_output.assert_called_once_with(x9.QUERY, use_hidd=False)
        device.set_feature_report.assert_not_called()

    def test_missing_wrong_model_and_multiple_receivers_are_rejected(self):
        for interfaces in ([], [interface(product="MCHOSE V9 PRO")],
                           [interface(pid=0x6046)], [interface(usage_page=0xFF82)],
                           [interface(input_len=2)], [interface(feature_len=64)],
                           [interface(), interface(path="second-x9")]):
            with patch.object(x9.hw, "HidDevice") as open_device:
                result = x9.read_battery(interfaces)
            self.assertFalse(result.ok)
            open_device.assert_not_called()

    def test_timeout_does_not_turn_into_zero_percent_and_retries_are_bounded(self):
        device = Mock()
        device.read_input.return_value = None
        with patch.object(x9, "device_lock", return_value=nullcontext()), \
                patch.object(x9.hw, "HidDevice") as open_device, \
                patch.object(x9.time, "monotonic", side_effect=itertools.count(0, 0.3)):
            open_device.return_value.__enter__.return_value = device
            result = x9.read_battery([interface()])
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        self.assertEqual(device.write_output.call_count, 3)

    def test_unplug_during_query_becomes_a_read_error(self):
        with patch.object(x9, "device_lock", return_value=nullcontext()), \
                patch.object(x9.hw, "HidDevice", side_effect=OSError("disconnected")):
            result = x9.read_battery([interface()])
        self.assertFalse(result.ok)
        self.assertIsNone(result.level)
        self.assertIn("disconnected", result.detail)

    def test_catalog_does_not_assume_other_model_with_same_id_is_supported(self):
        with patch.object(ds.hw, "enumerate_hid", return_value=[interface(product="Other Headset")]):
            device = ds.discover_devices()["3837:6045"]
        self.assertFalse(device.supported)
        self.assertEqual(device.title, "Other Headset")


if __name__ == "__main__":
    unittest.main()
