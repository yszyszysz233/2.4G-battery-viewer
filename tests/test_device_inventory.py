import unittest
from unittest.mock import Mock, patch

import device_service as ds
import hid_win as hw
import my_devices


def interface(vid=0x046D, pid=0xC547, product="USB Receiver", **changes):
    values = dict(path="inventory-test", vid=vid, pid=pid, product=product,
                  manufacturer="Test manufacturer", serial="", version=0,
                  usage_page=0xFF00, usage=2, input_len=20, output_len=20, feature_len=0)
    values.update(changes)
    return hw.HidInterface(**values)


class DeviceInventoryTests(unittest.TestCase):
    def test_empty_inventory_does_not_show_predefined_supported_models(self):
        with patch.object(hw, "enumerate_hid", return_value=[]):
            self.assertEqual(ds.discover_devices(), {})

    def test_reported_names_are_preserved_for_supported_models(self):
        interfaces = [interface(), interface(0x320F, 0x5088, "VGN S99 2.4G Dongle",
                                             usage_page=0xFF1C, usage=0x92, input_len=64, output_len=64),
                      interface(0x3837, 0x6045, "MCHOSE X9", usage_page=0xFF90,
                                usage=1, input_len=64, output_len=64)]
        with patch.object(hw, "enumerate_hid", return_value=interfaces):
            devices = ds.discover_devices()
        self.assertEqual({key: d.title for key, d in devices.items()},
                         {"046d:c547": "USB Receiver", "320f:5088": "VGN S99 2.4G Dongle",
                          "3837:6045": "MCHOSE X9"})
        self.assertTrue(all(d.supported for d in devices.values()))

    def test_wired_mouse_and_receiver_remain_distinct_with_connection_hints(self):
        with patch.object(hw, "enumerate_hid", return_value=[interface(), interface(pid=0xC094, product="PRO X Wireless")]):
            devices = ds.discover_devices()
        self.assertEqual(set(devices), {"046d:c547", "046d:c094"})
        self.assertEqual(devices["046d:c094"].title, "PRO X Wireless")
        self.assertIn("USB", devices["046d:c094"].connection)
        self.assertIn("接收器", devices["046d:c547"].connection)
        self.assertFalse(devices["046d:c094"].supported)

    def test_real_unknown_and_unnamed_devices_are_listed_but_virtual_devices_are_excluded(self):
        interfaces = [interface(0x0B05, 0x19AF, "AURA LED Controller"),
                      interface(pid=0xC232, product=""),
                      interface(0x00FF, 0xBACC, "Virtual Multitouch Device")]
        with patch.object(hw, "enumerate_hid", return_value=interfaces):
            devices = ds.discover_devices()
        self.assertEqual(set(devices), {"0b05:19af", "046d:c232"})
        self.assertTrue(devices["046d:c232"].title)
        self.assertFalse(any(d.supported for d in devices.values()))

    def test_multiple_collections_do_not_duplicate_one_device(self):
        with patch.object(hw, "enumerate_hid", return_value=[interface(), interface(usage_page=1, usage=2)]):
            devices = ds.discover_devices()
        self.assertEqual(list(devices), ["046d:c547"])
        self.assertEqual(len(devices["046d:c547"].interfaces), 2)

    def test_unplug_removes_device_from_catalog_but_preserves_saved_selection_name(self):
        cfg = {"selected_devices": ["046d:c547", ""], "tray_devices": ["046d:c547"],
               "device_labels": {"046d:c547": "USB Receiver"}}
        with patch.object(hw, "enumerate_hid", side_effect=[[interface()], []]):
            self.assertIn("046d:c547", ds.discover_devices())
            devices = ds.discover_devices()
        self.assertEqual(devices, {})
        reading = ds.read_selected(cfg["selected_devices"], devices, cfg["device_labels"])["046d:c547"]
        self.assertEqual((reading.title, reading.state), ("USB Receiver", "offline"))
        self.assertEqual(ds.tray_device_keys(cfg, devices), ["046d:c547"])
        self.assertEqual(devices, {})  # Remembered selections must not repopulate the inventory.

    def test_disconnected_card_does_not_inject_history_into_dropdown_choices(self):
        card = my_devices.DeviceCard.__new__(my_devices.DeviceCard)
        card.combo, card.choice = {}, Mock()
        card.set_options({"不显示设备": "", "VGN S99 2.4G Dongle": "320f:5088"},
                         "046d:c547", "USB Receiver")
        self.assertEqual(card.combo["values"], ["不显示设备", "VGN S99 2.4G Dongle"])
        card.choice.set.assert_called_once_with("USB Receiver · 未连接")


if __name__ == "__main__":
    unittest.main()
