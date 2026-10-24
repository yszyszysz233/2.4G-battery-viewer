# -*- coding: utf-8 -*-
"""tray_app.py —— 托盘电量监视器（纯 ctypes，无第三方依赖）

界面约定
--------
* 通知区域最多三个图标：小米鼠标（纯数字）、雷蛇耳机 / 赛睿耳机（数字带深色底板）。
  没插接收器的那一类不显示图标（三类都没插时全部显示 "--"，免得托盘里什么都看不到）。
  图标颜色随电量变化：绿 ≥60 / 黄 30-59 / 红 <30。
* **右键图标 → 菜单**（打开界面 / 立即刷新 / 关闭程式）。
* **双击图标 → 打开/恢复简洁界面**。
* 左键单击 → 立刻刷新一次。
* 悬停 tooltip：开启「托盘显示电量%」时以设备名为主（可带电量）。
* 「托盘显示电量%」关闭时恢复仅图标（色块）外观。

其它
----
* 开机自启用「启动」文件夹里的快捷方式（`电量托盘.lnk`，任务管理器可见）+ pythonw.exe。
* 低 CPU：SetTimer 驱动，空闲不轮询；读取在后台线程；默认 60 秒一轮。
* **不写日志文件**：只有 `--debug` 时才往控制台打印。
* 全 ctypes 调 shell32/user32，不依赖 pystray / Pillow。

启动：
    pythonw tray_app.py        # 静默驻留（开机自启用的就是这个）
    python  tray_app.py --debug   # 前台运行并打印过程信息（排查问题用）
    python  tray_app.py --status
    python  tray_app.py --quit
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import threading
import time
import traceback
from ctypes import wintypes
from typing import Optional

import hid_win as hw
import devices
import win_icon
import tray_common as tc
import tray_prefs as prefs

BASE_DIR = tc.BASE_DIR
APP_NAME = tc.APP_NAME
MUTEX_NAME = tc.MUTEX_NAME
WND_CLASS = tc.WND_CLASS

USER32 = tc.USER32
KERNEL32 = tc.KERNEL32
SHELL32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111
WM_QUIT = 0x0012
WM_CLOSE = 0x0010
WM_APP = 0x8000
MSG_TRAY_CALLBACK = WM_APP + 1
MSG_REFRESH_DONE = WM_APP + 2

# 托盘右键菜单命令
IDM_OPEN = 1001
IDM_REFRESH = 1002
IDM_QUIT = 1003

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04

SW_RESTORE = 9

# --------------------------------------------------------------------------
# Win32 结构体与原型
# --------------------------------------------------------------------------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", ctypes.c_void_p),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", ctypes.c_void_p),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", ctypes.c_void_p),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", hw.GUID),
        ("hBalloonIcon", ctypes.c_void_p),
    ]


class MSG(ctypes.Structure):
    _fields_ = [("hWnd", ctypes.c_void_p), ("message", wintypes.UINT),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                ("pt_y", wintypes.LONG)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


USER32.RegisterClassExW.restype = wintypes.WORD
USER32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
USER32.CreateWindowExW.restype = ctypes.c_void_p
USER32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
USER32.DefWindowProcW.restype = ctypes.c_ssize_t
USER32.DefWindowProcW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                  ctypes.c_size_t, ctypes.c_ssize_t]
USER32.DestroyWindow.restype = wintypes.BOOL
USER32.DestroyWindow.argtypes = [ctypes.c_void_p]
USER32.SetTimer.restype = ctypes.c_size_t
USER32.SetTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.UINT,
                            ctypes.c_void_p]
USER32.KillTimer.restype = wintypes.BOOL
USER32.KillTimer.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
USER32.GetMessageW.restype = ctypes.c_int
USER32.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                               wintypes.UINT, wintypes.UINT]
USER32.PostQuitMessage.restype = None
USER32.PostQuitMessage.argtypes = [ctypes.c_int]
USER32.TranslateMessage.restype = wintypes.BOOL
USER32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
USER32.DispatchMessageW.restype = ctypes.c_ssize_t
USER32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
USER32.CreatePopupMenu.restype = ctypes.c_void_p
USER32.CreatePopupMenu.argtypes = []
USER32.AppendMenuW.restype = wintypes.BOOL
USER32.AppendMenuW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_size_t,
                               wintypes.LPCWSTR]
USER32.TrackPopupMenu.restype = ctypes.c_int
USER32.TrackPopupMenu.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                                  ctypes.c_void_p]
USER32.DestroyMenu.restype = wintypes.BOOL
USER32.DestroyMenu.argtypes = [ctypes.c_void_p]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]

SHELL32.Shell_NotifyIconW.restype = wintypes.BOOL
SHELL32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]

KERNEL32.GetModuleHandleW.restype = ctypes.c_void_p
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                  wintypes.LPCWSTR]

TRAY_ICONS = [
    # (uID, key, 提示名, 是否带底板)
    (1, "xiaomi", "小米鼠标", False),
    (2, "razer", "雷蛇耳机", True),
    (3, "steelseries", "赛睿耳机", True),
]


# --------------------------------------------------------------------------
# 设备读取：devices.py（面板和托盘共用）
# --------------------------------------------------------------------------
def read_levels() -> dict[str, tuple[Optional[int], str, bool]]:
    """-> {key: (电量或 None, 说明, 是否找到接收器)}"""
    out = {}
    for key, r in devices.read_all_dict().items():
        if r.ok:
            note = ("约" if r.approx else "") + (f"，{r.status}" if r.status == "充电中" else "")
            out[key] = (r.level, note, True)
        else:
            out[key] = (None, r.error or r.status or "未知", r.found)
    return out


# --------------------------------------------------------------------------
# 托盘程序
# --------------------------------------------------------------------------
class TrayApp:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.cfg = tc.load_config()
        self.hwnd: Optional[int] = None
        self.wndproc_ref = None
        self.busy = False
        self.levels = {k: (None, "正在读取…", True) for _, k, _, _ in TRAY_ICONS}
        self.icons_added = False
        self.shown: set[int] = set()
        self._ui_launch_ts: float = 0.0  # 防双击连开多个面板
        self.taskbar_created_msg = USER32.RegisterWindowMessageW("TaskbarCreated")
        self.msg_quit = USER32.RegisterWindowMessageW(tc.MSG_QUIT)
        self.msg_reload = USER32.RegisterWindowMessageW(tc.MSG_RELOAD)
        self.log("启动，配置 = " + json.dumps(self.cfg, ensure_ascii=False))

    # ---- 输出（默认不落盘）---------------------------------------------
    def log(self, msg: str) -> None:
        """只在 --debug 时输出到控制台。

        默认**不写任何日志文件**（不占磁盘也不留痕）；需要排查问题时用
        `python tray_app.py --debug` 前台跑一次就能看到全部输出。
        """
        if self.debug:
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)

    # ---- 图标 ----------------------------------------------------------
    @staticmethod
    def _color(level: Optional[int]) -> tuple[int, int, int]:
        if level is None:
            return (150, 155, 160)
        if level >= 40:
            return (93, 220, 138)
        if level >= 11:
            return (245, 196, 81)
        return (255, 107, 107)

    def _nid(self, uID: int):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = uID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = MSG_TRAY_CALLBACK
        return nid

    def _show_percent(self) -> bool:
        return bool(self.cfg.get("tray_show_percent", True))

    def _apply_icon(self, action: int, uID: int, key: str, title: str,
                    plate: bool) -> bool:
        level, note, _found = self.levels[key]
        show_pct = self._show_percent()
        label = prefs.icon_text(level, show_pct)
        # 仅图标模式统一带底板，方便辨认色块
        use_plate = plate or (not show_pct)
        nid = self._nid(uID)
        nid.hIcon = win_icon.make_hicon(label, self._color(level), use_plate)
        tip = prefs.icon_tooltip(title, level, note, show_pct)
        if show_pct and level is not None:
            tip = f"{tip}（{time.strftime('%H:%M:%S')}）"
        nid.szTip = tip[:127]                 # szTip 最多 128 个宽字符（含结尾 0）
        return bool(SHELL32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    def _visible(self) -> list:
        """只显示当前检测到接收器的设备；一个都没有则不占托盘。"""
        return prefs.visible_icon_entries(TRAY_ICONS, self.levels)

    def add_icons(self) -> None:
        self.shown = set()
        for uID, key, title, plate in self._visible():
            ok = self._apply_icon(NIM_ADD, uID, key, title, plate)
            if ok:
                self.shown.add(uID)
            self.log(f"NIM_ADD {title} -> {'ok' if ok else '失败'}")
        self.icons_added = True

    def _delete_icon(self, uID: int) -> None:
        nid = self._nid(uID)
        nid.uFlags = 0
        SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.shown.discard(uID)

    def update_icons(self) -> None:
        if not self.icons_added:
            self.add_icons()
            return
        visible = self._visible()
        want = {t[0] for t in visible}
        for uID in list(self.shown - want):
            self._delete_icon(uID)
        for uID, key, title, plate in visible:
            if uID in self.shown:
                self._apply_icon(NIM_MODIFY, uID, key, title, plate)
            elif self._apply_icon(NIM_ADD, uID, key, title, plate):
                self.shown.add(uID)

    def remove_icons(self) -> None:
        for uID, _, _, _ in TRAY_ICONS:
            self._delete_icon(uID)
        self.icons_added = False

    # ---- 定时器 / 刷新 --------------------------------------------------
    def apply_timer(self) -> None:
        if not self.hwnd:
            return
        USER32.KillTimer(self.hwnd, 1)
        USER32.SetTimer(self.hwnd, 1, int(self.cfg["interval"]) * 1000, None)

    def apply_config(self) -> None:
        """同步界面里改过的设置。

        注意：开机自启这里**只读不改**——托盘绝不替用户动系统设置。
        （早先的版本会自动"纠正"，结果一次读到不一致就把用户的启动项删了。）
        """
        new = tc.load_config()
        interval_changed = new["interval"] != self.cfg["interval"]
        percent_changed = bool(new.get("tray_show_percent")) != bool(
            self.cfg.get("tray_show_percent"))
        self.cfg = new
        if interval_changed:
            self.log(f"刷新间隔 -> {new['interval']} 秒")
            self.apply_timer()
        if percent_changed:
            self.log(f"托盘显示电量% -> {bool(new.get('tray_show_percent'))}")
            self.update_icons()

        actual = tc.is_autostart_enabled()
        if bool(self.cfg.get("autostart")) != actual:
            # 只把真实状态写回配置，让界面上的勾选和任务管理器保持一致
            self.cfg["autostart"] = actual
            tc.save_config(self.cfg)
            self.log(f"开机自启实际为 {actual}，已同步到配置（未改动系统设置）")

    def refresh_now(self) -> None:
        if self.busy or not self.hwnd:
            return
        self.busy = True
        threading.Thread(target=self._worker, args=(self.hwnd,), daemon=True).start()

    def _worker(self, hwnd: int) -> None:
        try:
            self.levels.update(read_levels())
        except Exception:
            self.log("读取异常:\n" + traceback.format_exc())
        USER32.PostMessageW(hwnd, MSG_REFRESH_DONE, 0, 0)

    def on_refresh_done(self) -> None:
        self.busy = False
        self.log("刷新完成：" + "  ".join(
            f"{title}={self.levels[key][0]}（{self.levels[key][1] or 'ok'}）"
            for _, key, title, _ in TRAY_ICONS))
        self.update_icons()

    # ---- 打开简洁界面 ---------------------------------------------------
    def open_ui(self, minimized: bool = False) -> None:
        """打开或恢复唯一的简洁界面；绝不并行再起一个 BatteryViewer。"""
        hwnd = tc.ui_hwnd()
        if hwnd:
            self._ui_launch_ts = 0.0
            if minimized:
                USER32.ShowWindow(hwnd, 0)          # SW_HIDE
            else:
                tc.focus_or_show_ui()
            self.log("简洁界面已在前台" if not minimized else "简洁界面保持隐藏")
            return
        # 窗口标题尚未挂上时，避免连点再 spawn
        if not prefs.should_spawn_ui(False, self._ui_launch_ts, time.time()):
            self.log("简洁界面正在启动，跳过重复打开")
            return
        import subprocess
        try:
            if tc.is_frozen():
                cmd = [tc.panel_target()]
            else:
                cmd = [tc.pythonw_path(), tc.panel_target()]
            if minimized:
                cmd = list(cmd) + ["--minimized"]
            subprocess.Popen(cmd, cwd=BASE_DIR, creationflags=0x00000008)
            self._ui_launch_ts = time.time()
            self.log("已打开简洁界面" + ("（最小化）" if minimized else ""))
        except Exception as e:
            self._ui_launch_ts = 0.0
            self.log(f"打开界面失败：{e}")

    def show_menu(self) -> None:
        """右键菜单：打开界面 / 刷新 / 关闭程式（真正退出，不是藏到托盘）。"""
        menu = USER32.CreatePopupMenu()
        if not menu:
            return
        try:
            USER32.AppendMenuW(menu, MF_STRING, IDM_OPEN, "打开界面")
            USER32.AppendMenuW(menu, MF_STRING, IDM_REFRESH, "立即刷新")
            USER32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            USER32.AppendMenuW(menu, MF_STRING, IDM_QUIT, "关闭程式")
            pt = POINT()
            USER32.GetCursorPos(ctypes.byref(pt))
            # 让菜单能正确收回：先置前台再 TrackPopupMenu
            USER32.SetForegroundWindow(self.hwnd)
            cmd = USER32.TrackPopupMenu(
                menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                pt.x, pt.y, 0, self.hwnd, None)
            USER32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
            if cmd == IDM_OPEN:
                self.open_ui()
            elif cmd == IDM_REFRESH:
                self.refresh_now()
            elif cmd == IDM_QUIT:
                self.quit_all()
        finally:
            USER32.DestroyMenu(menu)

    def quit_ui(self) -> None:
        """强制关掉简洁界面（绕过「关闭=最小化到托盘」）。"""
        hwnd = tc.ui_hwnd()
        if not hwnd:
            return
        # DestroyWindow 不走 Tk 的 WM_DELETE_WINDOW，避免又被藏起来
        USER32.DestroyWindow(hwnd)
        self.log("已强制关闭简洁界面")

    def quit_all(self) -> None:
        """托盘菜单「关闭程式」：界面 + 托盘都真正退出。"""
        self.quit_ui()
        self.quit()

    def quit(self) -> None:
        self.log("退出")
        self.remove_icons()
        try:
            win_icon.destroy_all()
            win_icon.cleanup_files()
        except Exception:
            self.log("清理图标时出错（忽略）：\n" + traceback.format_exc())
        if self.hwnd:
            # DestroyWindow 会触发 WM_DESTROY -> PostQuitMessage，消息循环随之结束
            USER32.DestroyWindow(self.hwnd)

    # ---- 窗口过程 -------------------------------------------------------
    def wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == MSG_TRAY_CALLBACK:
            event = int(lparam) & 0xFFFF
            if event == WM_RBUTTONUP:
                self.show_menu()                   # 右键：菜单（含真正退出）
            elif event == WM_LBUTTONDBLCLK:
                self.open_ui()                     # 双击：打开/恢复界面
            elif event == WM_LBUTTONUP:
                self.refresh_now()                 # 左键单击：立刻刷新
            return 0
        if msg == MSG_REFRESH_DONE:
            self.on_refresh_done()
            return 0
        if msg == WM_TIMER:
            self.apply_config()                    # 顺手同步一下界面里改的设置
            self.refresh_now()
            return 0
        if msg == self.msg_quit:
            self.quit_all()
            return 0
        if msg == self.msg_reload:
            self.apply_config()
            return 0
        if msg == self.taskbar_created_msg:
            self.log("任务栏重建，重新添加图标")
            self.add_icons()
            return 0
        if msg == WM_DESTROY:
            # 重要：销毁窗口后必须让消息循环退出，否则进程会残留成"僵尸"
            # ——它已经没有窗口/图标了，却还占着单实例锁，导致托盘再也起不来。
            USER32.PostQuitMessage(0)
            return 0
        return USER32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 启动 ----------------------------------------------------------
    def run(self) -> int:
        hinst = KERNEL32.GetModuleHandleW(None)
        self.wndproc_ref = WNDPROC(self.wnd_proc)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self.wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = WND_CLASS
        if not USER32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err not in (0, 1410):
                self.log(f"RegisterClassExW 失败 err={err}")
                return 1

        hwnd = USER32.CreateWindowExW(0, WND_CLASS, APP_NAME, 0, 0, 0, 0, 0,
                                      None, None, hinst, None)
        if not hwnd:
            self.log(f"CreateWindowExW 失败 err={ctypes.get_last_error()}")
            return 1
        self.hwnd = hwnd

        self.add_icons()
        self.apply_config()
        self.apply_timer()
        self.refresh_now()
        # 开机自启 / 「开机自启直接最小化」：托盘起来后把面板藏着启动
        if prefs.should_start_minimized(self.cfg):
            self.open_ui(minimized=True)

        msg = MSG()
        while USER32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            USER32.TranslateMessage(ctypes.byref(msg))
            USER32.DispatchMessageW(ctypes.byref(msg))
        self.log("消息循环结束")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="托盘电量监视器")
    parser.add_argument("--debug", action="store_true", help="前台运行并输出日志")
    parser.add_argument("--status", action="store_true", help="打印当前状态")
    parser.add_argument("--quit", action="store_true", help="让正在运行的实例退出")
    args = parser.parse_args()

    if args.status:
        print("配置：", json.dumps(tc.load_config(), ensure_ascii=False))
        print("开机自启：", tc.is_autostart_enabled())
        print("启动命令：", tc.autostart_command())
        print("托盘是否在运行：", tc.is_tray_running())
        return 0
    if args.quit:
        ok = tc.post_to_tray(tc.MSG_QUIT)
        print("已通知托盘退出" if ok else "托盘没在运行")
        return 0

    # 单实例
    KERNEL32.CreateMutexW(None, False, MUTEX_NAME)
    if ctypes.get_last_error() == 183:
        if tc.is_tray_running():
            # 真有一个正常实例在跑：让它重读配置
            tc.post_to_tray(tc.MSG_RELOAD)
            print("已经在运行了（托盘图标就是它；双击打开界面，右键可关闭程式）")
            return 0
        # 有人占着锁但已经没有窗口 —— 上一版留下的僵尸进程，清掉后继续启动
        tc.cleanup_zombie()

    tc.write_pid()

    try:
        USER32.SetProcessDPIAware()            # 图标不糊
    except Exception:
        pass

    app = TrayApp(debug=args.debug)
    try:
        return app.run()
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        tc.remove_pid()


if __name__ == "__main__":
    sys.exit(main())
