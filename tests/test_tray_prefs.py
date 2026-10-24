# -*- coding: utf-8 -*-
"""托盘设置纯逻辑单测（不依赖 Win32 / tkinter）。"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import tray_prefs as prefs  # noqa: E402


class TestTrayPrefs(unittest.TestCase):
    def test_default_keys(self):
        for k in ("interval", "autostart", "tray_show_percent",
                  "start_minimized", "close_to_tray"):
            self.assertIn(k, prefs.DEFAULT_CONFIG)

    def test_icon_text_percent_on(self):
        self.assertEqual(prefs.icon_text(82, True), "82")
        self.assertEqual(prefs.icon_text(0, True), "0")
        self.assertEqual(prefs.icon_text(None, True), "--")

    def test_icon_text_percent_off(self):
        self.assertEqual(prefs.icon_text(82, False), "")
        self.assertEqual(prefs.icon_text(None, False), "")

    def test_tooltip_percent_on_named(self):
        tip = prefs.icon_tooltip("小米鼠标", 70, "", True)
        self.assertTrue(tip.startswith("小米鼠标"))
        self.assertIn("70%", tip)

    def test_tooltip_percent_on_no_level(self):
        self.assertEqual(prefs.icon_tooltip("雷蛇耳机", None, "无接收器", True),
                         "雷蛇耳机：无接收器")
        self.assertEqual(prefs.icon_tooltip("雷蛇耳机", None, "", True), "雷蛇耳机")

    def test_tooltip_percent_off(self):
        tip = prefs.icon_tooltip("赛睿耳机", 40, "约", False)
        self.assertIn("赛睿耳机", tip)
        self.assertIn("40%", tip)

    def test_should_start_minimized(self):
        self.assertTrue(prefs.should_start_minimized({}, True))
        self.assertTrue(prefs.should_start_minimized({"start_minimized": True}))
        self.assertFalse(prefs.should_start_minimized({"start_minimized": False}))
        self.assertFalse(prefs.should_start_minimized({}))

    def test_visible_icon_entries(self):
        icons = [
            (1, "xiaomi", "小米鼠标", False),
            (2, "razer", "雷蛇耳机", True),
            (3, "steelseries", "赛睿耳机", True),
        ]
        levels = {
            "xiaomi": (80, "", True),
            "razer": (None, "无", False),
            "steelseries": (50, "", True),
        }
        vis = prefs.visible_icon_entries(icons, levels)
        self.assertEqual([t[1] for t in vis], ["xiaomi", "steelseries"])
        self.assertEqual(prefs.visible_icon_entries(icons, {
            "xiaomi": (None, "", False),
            "razer": (None, "", False),
            "steelseries": (None, "", False),
        }), [])


class TestRenderRgbaEmpty(unittest.TestCase):
    """win_icon.render_rgba 在无 Win32 时仍可测像素逻辑；LoadImage 才会碰 DLL。

    这里用 importlib 只加载纯函数会很脆，改为复制关键断言：空文字长度。
    真正的 RGBA 路径在有 user32 的 Windows 上由手工验证。
    """

    def test_icon_text_drives_empty_mode(self):
        self.assertEqual(prefs.icon_text(55, False), "")
        self.assertNotEqual(prefs.icon_text(55, True), "")


if __name__ == "__main__":
    unittest.main()
