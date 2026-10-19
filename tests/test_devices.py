import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import device_service as ds
import logitech_hidpp as lh
import my_devices
import tray_app
import tray_common as tc


class ProtocolTests(unittest.TestCase):
    def test_real_gpw2_capture_uses_status_not_level_flags(self):
        reply = bytes.fromhex("11 01 06 1e 1b 02 00 00")
        state = lh.parse_battery(0x1004, reply, 6)
        self.assertEqual((state.level, state.status), (27, "discharging"))
        charging = lh.parse_battery(0x1004, reply[:6] + b"\x01\x00")
        self.assertEqual(charging.status, "charging")

    def test_voltage_is_big_endian_and_unknown_level_is_not_invented(self):
        state = lh.parse_battery(0x1001, bytes.fromhex("11 01 06 0d 0f a0 80"))
        self.assertEqual((state.level, state.voltage_mv, state.status), (None, 4000, "charging"))
        state = lh.parse_battery(0x1000, bytes.fromhex("11 01 06 0d 00 00 01"))
        self.assertIsNone(state.level)

    def test_rejects_invalid_and_truncated_percentage(self):
        for reply in (bytes.fromhex("11 01 06 1e ff 02 00 00"),
                      bytes.fromhex("11 01 06 1e 1b 02 00")):
            with self.assertRaises(lh.HidppError):
                lh.parse_battery(0x1004, reply)

    def test_delayed_root_reply_and_notifications_do_not_match(self):
        class Port:
            def write_output(port, request, **_):
                fn = request[3]
                port.replies = [
                    bytes([0x11, 1, 0, fn - 1, 3, 0, 0]),  # Earlier name lookup.
                    bytes([0x11, 2, 0, fn, 5, 0, 0]),      # Other receiver slot.
                    bytes([0x11, 1, 0, fn, 6, 0, 3]),      # Index 6, version 3.
                ]

            def read_input(port, **_):
                return port.replies.pop(0) if port.replies else None

        session = lh.HidppSession([])
        session.devices = [Port()]
        session.sequence = 7
        self.assertEqual(session.feature_index(0x1004), 6)

    def test_short_error_frame_is_recognized_for_long_request(self):
        port = Mock()
        session = lh.HidppSession([])
        session.devices = [port]
        session.sequence = 7
        port.read_input.return_value = bytes.fromhex("10 01 8f 00 08 08 00")
        with self.assertRaises(lh.HidppError) as raised:
            session.feature_index(0x1004)
        self.assertEqual(raised.exception.code, 8)


class SelectionTests(unittest.TestCase):
    def test_first_run_detects_supported_device_and_explicit_empty_stays_empty(self):
        devices = {"046d:c547": ds.Device("046d:c547", "GPW2", "logitech", interfaces=[object()])}
        self.assertEqual(ds.selected_devices({"selected_devices": None}, devices), ["046d:c547", ""])
        self.assertEqual(ds.selected_devices({"selected_devices": ["", ""]}, devices), ["", ""])

    def test_switching_to_other_card_swaps_without_duplicate_queries(self):
        self.assertEqual(ds.switch_slot(["gpw2", "other"], 0, "other"), ["other", "gpw2"])
        self.assertEqual(ds.switch_slot(["gpw2", ""], 1, "gpw2"), ["", "gpw2"])

    def test_unsupported_hardware_never_receives_vendor_commands(self):
        device = ds.Device("374a:a030", "Unadapted device", interfaces=[object()])
        with patch.object(ds.bt, "strategy_vendor_protocol") as vendor, \
                patch.object(ds.bt, "strategy_logitech_hidpp") as logitech:
            reading = ds.read_device(device)
        self.assertEqual(reading.state, "unsupported")
        self.assertIsNone(reading.level)
        vendor.assert_not_called()
        logitech.assert_not_called()

    def test_settings_survive_restart_and_preserve_selected_offline_name(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tc, "BASE_DIR", directory), \
                patch.object(tc, "CONFIG_PATH", str(Path(directory) / "config.json")):
            tc.update_config(selected_devices=["046d:c547", "374a:a030"],
                             device_labels={"374a:a030": "My device"})
            tc.update_config(interval=120, auto_refresh=False)
            cfg = tc.load_config()
            self.assertEqual(cfg["selected_devices"], ["046d:c547", "374a:a030"])
            self.assertEqual(cfg["device_labels"]["374a:a030"], "My device")
            self.assertEqual(cfg["interval"], 120)
            self.assertFalse(cfg["auto_refresh"])
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_malformed_settings_and_duplicate_selection(self):
        cfg = tc.normalize_config({"interval": "oops", "selected_devices": ["a", "a", "b"],
                                   "device_labels": [], "auto_refresh": "false"})
        self.assertEqual(cfg["interval"], 60)
        self.assertEqual(cfg["selected_devices"], ["a", ""])
        self.assertTrue(cfg["auto_refresh"])

    def test_old_async_read_cannot_overwrite_new_selection(self):
        dashboard = my_devices.Dashboard.__new__(my_devices.Dashboard)
        dashboard.generation = 2
        dashboard.pending = False
        dashboard.refresh_button = Mock()
        dashboard.cards = [Mock(), Mock()]
        dashboard.refresh = Mock()
        dashboard.apply_read(1, {}, ["old", ""], {}, "")
        dashboard.cards[0].update.assert_not_called()
        dashboard.refresh.assert_called_once()

    def test_tray_empty_slot_has_no_second_icon(self):
        tray = tray_app.TrayApp.__new__(tray_app.TrayApp)
        tray.cfg = tc.normalize_config({})
        specs = tray.icon_specs(["046d:c547", ""], {})
        self.assertEqual([(uid, key) for uid, key, _, _ in specs], [(1, "046d:c547")])
        self.assertEqual(len(tray.icon_specs(["", ""], {})), 1)  # An entry point stays accessible.


if __name__ == "__main__":
    unittest.main()
