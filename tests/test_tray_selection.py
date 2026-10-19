import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import device_service as ds
import my_devices
import tray_app
import tray_common as tc


class TraySelectionTests(unittest.TestCase):
    def test_legacy_selection_migrates_before_home_cards_are_changed(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tc, "BASE_DIR", directory), \
                patch.object(tc, "CONFIG_PATH", str(Path(directory) / "config.json")):
            Path(tc.CONFIG_PATH).write_text(json.dumps({"selected_devices": ["a", "b"]}), encoding="utf-8")
            cfg = tc.update_config(selected_devices=["c", ""])
            self.assertEqual(cfg["tray_devices"], ["a", "b"])
            self.assertEqual(tc.load_config()["tray_devices"], ["a", "b"])
            self.assertEqual(tc.load_config()["selected_devices"], ["c", ""])

    def test_multiple_tray_devices_survive_restart_and_explicit_empty_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(tc, "BASE_DIR", directory), \
                patch.object(tc, "CONFIG_PATH", str(Path(directory) / "config.json")):
            tc.update_config(tray_devices=["a", "b", "c", "a", "", None, 42])
            self.assertEqual(tc.load_config()["tray_devices"], ["a", "b", "c"])
            tc.update_config(tray_devices=[])
            tc.update_config(selected_devices=["a", "b"])
            self.assertEqual(tc.load_config()["tray_devices"], [])

    def test_first_run_enables_all_supported_connected_devices_without_two_device_limit(self):
        devices = {key: ds.Device(key, key, "vendor", interfaces=[object()]) for key in ("a", "b", "c")}
        devices["offline"] = ds.Device("offline", "offline", "vendor")
        devices["unknown"] = ds.Device("unknown", "unknown", interfaces=[object()])
        cfg = tc.normalize_config({})
        self.assertEqual(ds.tray_device_keys(cfg, devices), ["a", "b", "c"])
        self.assertEqual(ds.selected_devices(cfg, devices), ["a", "b"])
        self.assertEqual(ds.tray_device_keys({"tray_devices": []}, devices), [])

    def test_offline_enabled_device_is_not_removed_from_selection(self):
        cfg = tc.normalize_config({"tray_devices": ["3837:6045"]})
        self.assertEqual(ds.tray_device_keys(cfg, {}), ["3837:6045"])
        reading = ds.read_selected(ds.tray_device_keys(cfg, {}), {})["3837:6045"]
        self.assertFalse(reading.ok)
        self.assertEqual(reading.state, "offline")

    def test_more_than_two_devices_generate_distinct_icons_and_empty_keeps_entry(self):
        tray = tray_app.TrayApp.__new__(tray_app.TrayApp)
        tray.cfg = tc.normalize_config({})
        specs = tray.icon_specs(["a", "b", "c", "d", "a"], {})
        self.assertEqual([(uid, key) for uid, key, _, _ in specs], [(1, "a"), (2, "b"), (3, "c"), (4, "d")])
        self.assertEqual([(uid, key) for uid, key, _, _ in tray.icon_specs([], {})], [(1, "overview")])

    def test_home_changes_do_not_change_tray_icons_or_trigger_new_reads(self):
        tray = tray_app.TrayApp.__new__(tray_app.TrayApp)
        tray.cfg = tc.normalize_config({"tray_devices": ["a", "b", "c"], "selected_devices": ["a", "b"]})
        tray.tray_keys = ["a", "b", "c"]
        tray.apply_timer, tray.refresh_now, tray.remove_icons = Mock(), Mock(), Mock()
        new = dict(tray.cfg, selected_devices=["other", ""])
        with patch.object(tc, "load_config", return_value=new):
            tray.apply_config()
        tray.refresh_now.assert_not_called()
        tray.remove_icons.assert_not_called()
        self.assertEqual(tray.tray_keys, ["a", "b", "c"])

    def test_worker_reads_tray_selection_instead_of_home_selection(self):
        tray = tray_app.TrayApp.__new__(tray_app.TrayApp)
        tray.log = Mock()
        cfg = tc.normalize_config({"tray_devices": ["a", "b", "c"], "selected_devices": ["other", ""]})
        with patch.object(ds, "discover_devices", return_value={}), \
                patch.object(ds, "read_selected", return_value={}) as read, \
                patch.object(tray_app.USER32, "PostMessageW"):
            tray._worker(123, 7, cfg)
        read.assert_called_once_with(["a", "b", "c"], {}, cfg["device_labels"])
        self.assertEqual(tray.completed[:2], (7, ["a", "b", "c"]))

    def test_disabling_one_device_discards_an_old_in_flight_result(self):
        tray = tray_app.TrayApp.__new__(tray_app.TrayApp)
        tray.cfg = tc.normalize_config({"tray_devices": ["a", "b", "c"]})
        tray.tray_keys, tray.revision = ["a", "b", "c"], 0
        tray.remove_icons, tray.add_icons, tray.update_icons = Mock(), Mock(), Mock()
        tray.refresh_now = Mock()
        new = dict(tray.cfg, tray_devices=["a", "c"])
        with patch.object(tc, "load_config", return_value=new):
            tray.apply_config()
        self.assertEqual(tray.tray_keys, ["a", "c"])
        self.assertEqual([key for _, key, _, _ in tray.icons], ["a", "c"])
        tray.completed = (0, ["a", "b", "c"], {}, {}, "")
        tray.refresh_pending = True
        tray.on_refresh_done()
        tray.update_icons.assert_not_called()
        self.assertEqual(tray.tray_keys, ["a", "c"])

    def test_ui_toggles_only_the_target_device_and_starts_background(self):
        dashboard = my_devices.Dashboard.__new__(my_devices.Dashboard)
        dashboard.cfg = tc.normalize_config({"selected_devices": ["a", "b"], "tray_devices": ["a", "b"]})
        dashboard.devices = {key: ds.Device(key, key) for key in ("a", "b", "c")}
        dashboard.slots = ["a", "b"]
        dashboard.update_tray_choices, dashboard.start_tray = Mock(), Mock()

        def persist(**changes):
            dashboard.cfg.update(changes)
            return True
        dashboard.persist = Mock(side_effect=persist)
        self.assertTrue(dashboard.set_tray_device("c", True))
        self.assertEqual(dashboard.cfg["tray_devices"], ["a", "b", "c"])
        dashboard.start_tray.assert_called_once()
        self.assertTrue(dashboard.set_tray_device("b", False))
        self.assertEqual(dashboard.cfg["tray_devices"], ["a", "c"])
        self.assertEqual(dashboard.slots, ["a", "b"])
        self.assertEqual(dashboard.cfg["selected_devices"], ["a", "b"])

    def test_failed_save_does_not_enable_a_device_or_start_background(self):
        dashboard = my_devices.Dashboard.__new__(my_devices.Dashboard)
        dashboard.cfg = tc.normalize_config({"tray_devices": ["a"]})
        dashboard.devices = {"b": ds.Device("b", "b")}
        dashboard.persist = Mock(return_value=False)
        dashboard.start_tray, dashboard.update_tray_choices = Mock(), Mock()
        self.assertFalse(dashboard.set_tray_device("b", True))
        self.assertEqual(dashboard.cfg["tray_devices"], ["a"])
        dashboard.start_tray.assert_not_called()


if __name__ == "__main__":
    unittest.main()
