# -*- coding: utf-8 -*-
"""tray_app.py —— 托盘电量监视器（纯 ctypes，无第三方依赖）

界面约定
--------
* 通知区域显示主页所选设备的电量；空卡片不生成多余图标。
  图标颜色随电量变化：绿 ≥60 / 黄 30-59 / 红 <30。
* **右键图标 → 直接打开简洁界面**（界面里改设置：刷新间隔 / 开机自启 / 退出托盘）。
* 左键图标 → 立刻刷新一次。
* 悬停显示 tooltip：设备名 + 电量 + 更新时间。

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
import device_service as ds
import win_icon
import tray_common as tc

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
WM_APP = 0x8000
MSG_TRAY_CALLBACK = WM_APP + 1
MSG_REFRESH_DONE = WM_APP + 2

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

SHELL32.Shell_NotifyIconW.restype = wintypes.BOOL
SHELL32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]

KERNEL32.GetModuleHandleW.restype = ctypes.c_void_p
KERNEL32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
KERNEL32.CreateMutexW.restype = ctypes.c_void_p
KERNEL32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                  wintypes.LPCWSTR]

class TrayApp:
    def __init__(self, debug: bool = False):
        self.debug = debug
        self.cfg = tc.load_config()
        self.hwnd: Optional[int] = None
        self.wndproc_ref = None
        self.busy = False
        self.slots = self.cfg["selected_devices"]
        self.icons = self.icon_specs(self.slots or [], {})
        self.levels = {key: (None, "正在读取…") for _, key, _, _ in self.icons}
        self.added_icons = set()
        self.revision = 0
        self.refresh_pending = False
        self.completed = None
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
        if level >= 60:
            return (93, 220, 138)
        if level >= 30:
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

    def _apply_icon(self, action: int, uID: int, key: str, title: str,
                    plate: bool) -> bool:
        level, err = self.levels[key]
        text = str(level) if level is not None else "--"
        nid = self._nid(uID)
        nid.hIcon = win_icon.make_hicon(text, self._color(level), plate)
        nid.szTip = (f"{title}：{level}%（{time.strftime('%H:%M:%S')} 更新）"
                     if level is not None else f"{title}：{err or '未知'}")[:127]
        return bool(SHELL32.Shell_NotifyIconW(action, ctypes.byref(nid)))

    def icon_specs(self, slots, devices):
        icons = []
        for slot, key in enumerate(slots):
            if not key:
                continue
            title = (devices[key].title if key in devices
                     else self.cfg["device_labels"].get(key, ds.KNOWN.get(key, (key,))[0]))
            icons.append((slot + 1, key, title, slot == 1))
        return icons or [(1, "overview", tc.UI_TITLE, False)]

    def add_icons(self) -> None:
        for uid, key, title, plate in self.icons:
            self.levels.setdefault(key, (None, "请在主页选择设备"))
            if self._apply_icon(NIM_ADD, uid, key, title, plate):
                self.added_icons.add(uid)

    def update_icons(self) -> None:
        for uid, key, title, plate in self.icons:
            action = NIM_MODIFY if uid in self.added_icons else NIM_ADD
            if self._apply_icon(action, uid, key, title, plate):
                self.added_icons.add(uid)

    def remove_icons(self) -> None:
        for uid in self.added_icons:
            nid = self._nid(uid)
            SHELL32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.added_icons.clear()

    def apply_timer(self) -> None:
        if not self.hwnd:
            return
        USER32.KillTimer(self.hwnd, 1)
        if self.cfg["auto_refresh"]:
            USER32.SetTimer(self.hwnd, 1, int(self.cfg["interval"]) * 1000, None)

    def apply_config(self) -> None:
        new = tc.load_config()
        changed = new["selected_devices"] != self.slots
        timer_changed = any(new[k] != self.cfg[k] for k in ("interval", "auto_refresh"))
        self.cfg = new
        if timer_changed:
            self.apply_timer()
        if changed:
            self.revision += 1
            self.slots = new["selected_devices"]
            self.remove_icons()
            self.icons = self.icon_specs(self.slots or [], {})
            self.levels = {key: (None, "正在读取…") for _, key, _, _ in self.icons}
            self.add_icons()
            self.refresh_now()

    def refresh_now(self) -> None:
        if not self.hwnd:
            return
        if self.busy:
            self.refresh_pending = True
            return
        self.busy = True
        revision, cfg = self.revision, dict(self.cfg)
        threading.Thread(target=self._worker, args=(self.hwnd, revision, cfg), daemon=True).start()

    def _worker(self, hwnd, revision, cfg) -> None:
        try:
            devices = ds.discover_devices(cfg["device_labels"])
            slots = ds.selected_devices(cfg, devices)
            readings = ds.read_selected(slots, devices)
            self.completed = (revision, slots, devices, readings, "")
        except Exception as error:
            self.completed = (revision, cfg["selected_devices"] or [], {}, {}, str(error))
            self.log("读取异常:\n" + traceback.format_exc())
        USER32.PostMessageW(hwnd, MSG_REFRESH_DONE, 0, 0)

    def on_refresh_done(self) -> None:
        self.busy = False
        result, self.completed = self.completed, None
        if result and result[0] == self.revision:
            _, slots, devices, readings, error = result
            if self.slots is None and not error:
                try:
                    labels = {k: devices[k].title for k in slots if k in devices}
                    self.cfg = tc.update_config(selected_devices=slots, device_labels=labels)
                except OSError as save_error:
                    self.log(f"保存设备选择失败：{save_error}")
            self.slots = slots
            icons = self.icon_specs(slots, devices)
            changed = icons != self.icons
            if changed:
                self.remove_icons()
                self.icons = icons
            self.levels = {
                key: (readings[key].level, readings[key].error or readings[key].status)
                if key in readings else (None, error or "请在主页选择设备")
                for _, key, _, _ in self.icons
            }
            if changed:
                self.add_icons()
            else:
                self.update_icons()
            self.log("刷新完成：" + str(self.levels))
        if self.refresh_pending:
            self.refresh_pending = False
            self.refresh_now()

    # ---- 打开简洁界面 ---------------------------------------------------
    def open_ui(self) -> None:
        hwnd = tc.ui_hwnd()
        if hwnd:
            USER32.ShowWindow(hwnd, SW_RESTORE)
            USER32.SetForegroundWindow(hwnd)
            self.log("简洁界面已在前台")
            return
        import subprocess
        try:
            subprocess.Popen([tc.pythonw_path(),
                              os.path.join(BASE_DIR, "my_devices.py")],
                             cwd=BASE_DIR, creationflags=0x00000008)
            self.log("已打开简洁界面")
        except Exception as e:
            self.log(f"打开界面失败：{e}")

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
                self.open_ui()                     # 右键：直接打开界面
            elif event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.refresh_now()                 # 左键：立刻刷新
            return 0
        if msg == MSG_REFRESH_DONE:
            self.on_refresh_done()
            return 0
        if msg == WM_TIMER:
            self.apply_config()                    # 顺手同步一下界面里改的设置
            if not self.busy:
                self.refresh_now()
            return 0
        if msg == self.msg_quit:
            self.quit()
            return 0
        if msg == self.msg_reload:
            self.apply_config()
            return 0
        if msg == self.taskbar_created_msg:
            self.log("任务栏重建，重新添加图标")
            self.added_icons.clear()
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
            print("已经在运行了（右键电量托盘图标可打开主页）")
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
