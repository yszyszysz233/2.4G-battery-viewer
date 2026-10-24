# -*- coding: utf-8 -*-
"""tray_prefs.py —— 托盘/面板共用的纯设置逻辑（无 Win32 依赖，便于单测）

配置键与 DEFAULT_CONFIG 以这里为准；tray_common.load_config / save_config 复用。
"""

from __future__ import annotations

from typing import Optional

# 与 tray_config.json 字段一一对应
DEFAULT_CONFIG = {
    "interval": 60,              # 托盘轮询间隔（秒）
    "autostart": True,           # 开机自启
    "tray_show_percent": True,   # 在系统托盘显示电量百分比
    "start_minimized": False,    # 开机自启直接最小化（启动时隐藏主界面）
    "close_to_tray": False,      # 关闭 ui = 最小化到系统托盘
}


def icon_text(level: Optional[int], show_percent: bool) -> str:
    """托盘图标上的文字。

    show_percent=True  → 电量数字（或 "--"）
    show_percent=False → 空串，交给绘图层画纯色底板（旧的「仅图标」外观）
    """
    if not show_percent:
        return ""
    return str(level) if level is not None else "--"


def icon_tooltip(title: str, level: Optional[int], note: str,
                 show_percent: bool) -> str:
    """托盘悬停提示。开启百分比时以设备名为主，可附带电量。"""
    note = note or ""
    if show_percent:
        if level is not None:
            approx = "约 " if note.startswith("约") else ""
            charging = "（充电中）" if "充电中" in note else ""
            return f"{title} {approx}{level}%{charging}".strip()
        return title if not note else f"{title}：{note}"
    # 仅图标模式：保留较完整的旧式 tip
    if level is not None:
        approx = "约 " if note.startswith("约") else ""
        charging = "（充电中）" if "充电中" in note else ""
        return f"{title}：{approx}{level}%{charging}"
    return f"{title}：{note or '未知'}"


def should_start_minimized(cfg: dict, argv_minimized: bool = False) -> bool:
    """启动时是否直接隐藏主界面（依赖托盘）。"""
    if argv_minimized:
        return True
    return bool(cfg.get("start_minimized"))


def visible_icon_entries(icons: list, levels: dict) -> list:
    """只保留当前检测到接收器的条目；一个都没有则返回空（不占托盘）。

    icons: [(uID, key, title, plate), ...]
    levels: {key: (level, note, found)}
    """
    return [t for t in icons if levels.get(t[1], (None, "", False))[2]]


# 托盘 open_ui：防连点重复拉起面板（秒）
UI_LAUNCH_DEBOUNCE_SEC = 8.0


def should_spawn_ui(existing_hwnd: bool, launch_ts: float, now: float,
                    debounce: float = UI_LAUNCH_DEBOUNCE_SEC) -> bool:
    """是否该再 Popen 一个 BatteryViewer。

    已有窗口 → 否；刚启动过、窗口标题还没挂上 → 否（等现有进程）。
    """
    if existing_hwnd:
        return False
    if launch_ts and (now - launch_ts) < debounce:
        return False
    return True

