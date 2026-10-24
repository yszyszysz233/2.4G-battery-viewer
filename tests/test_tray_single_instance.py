# -*- coding: utf-8 -*-
"""面板单实例 / 防连开逻辑（纯函数，无 Win32）。"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import tray_prefs as prefs  # noqa: E402


class TestShouldSpawnUi(unittest.TestCase):
    def test_existing_hwnd_never_spawns(self):
        self.assertFalse(prefs.should_spawn_ui(True, 0.0, 100.0))
        self.assertFalse(prefs.should_spawn_ui(True, 99.0, 100.0))

    def test_fresh_launch_spawns(self):
        self.assertTrue(prefs.should_spawn_ui(False, 0.0, 100.0))

    def test_debounce_blocks_rapid_relaunch(self):
        launch_ts = 100.0
        now = launch_ts + 1.0
        self.assertFalse(prefs.should_spawn_ui(False, launch_ts, now))

    def test_after_debounce_allows_again(self):
        launch_ts = 100.0
        now = launch_ts + prefs.UI_LAUNCH_DEBOUNCE_SEC + 0.1
        self.assertTrue(prefs.should_spawn_ui(False, launch_ts, now))


class TestUiMutexNameConstant(unittest.TestCase):
    def test_mutex_names_differ(self):
        # 延迟 import：tray_common 在非 Windows 会加载 WinDLL，可能失败
        try:
            import tray_common as tc
        except (OSError, AttributeError, ImportError):
            self.skipTest("Win32 DLLs unavailable")
        self.assertIn("DeviceBatteryTray", tc.MUTEX_NAME)
        self.assertIn("DeviceBatteryViewer", tc.UI_MUTEX_NAME)
        self.assertNotEqual(tc.MUTEX_NAME, tc.UI_MUTEX_NAME)


if __name__ == "__main__":
    unittest.main()
