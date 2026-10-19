# -*- coding: utf-8 -*-
"""tray_common.py —— 托盘程序与界面共用的配置 / 开机自启 / 进程间通信

托盘程序（tray_app.py）和简洁界面（my_devices.py）是两个进程，
共用这里的配置文件与窗口消息，界面里改设置后能立刻通知托盘生效。
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from typing import Optional

import win_link
import win_shell

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "tray_config.json")
PID_FILE = os.path.join(BASE_DIR, "tray_pid.txt")

APP_NAME = "DeviceBatteryTray"
WND_CLASS = "DeviceBatteryTrayWnd"
MUTEX_NAME = "Global\\DeviceBatteryTray_single_instance"

# 界面标题（托盘用它来找到已打开的窗口）
UI_TITLE = "我的设备电量"

DEFAULT_CONFIG = {
    "interval": 60,        # 托盘轮询间隔（秒）
    "autostart": False,    # 开机自启只通过用户点击启用
    "auto_refresh": True,
    "selected_devices": None,  # None = 首次启动，按已连接设备初始化
    "device_labels": {},
}

INTERVAL_CHOICES = [(30, "30 秒"), (60, "1 分钟"), (120, "2 分钟"),
                    (300, "5 分钟"), (600, "10 分钟"), (1800, "30 分钟")]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

USER32 = ctypes.WinDLL("user32", use_last_error=True)
KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

USER32.FindWindowW.restype = ctypes.c_void_p
USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
USER32.PostMessageW.restype = wintypes.BOOL
USER32.PostMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                ctypes.c_size_t, ctypes.c_ssize_t]
USER32.RegisterWindowMessageW.restype = wintypes.UINT
USER32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
USER32.SetForegroundWindow.restype = wintypes.BOOL
USER32.SetForegroundWindow.argtypes = [ctypes.c_void_p]


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def normalize_config(data) -> dict:
    cfg = dict(DEFAULT_CONFIG, device_labels={})
    if not isinstance(data, dict):
        return cfg
    try:
        interval = int(data.get("interval", 60))
        cfg["interval"] = max(30, min(1800, interval))
    except (ValueError, TypeError, OverflowError):
        pass
    for key in ("autostart", "auto_refresh"):
        if isinstance(data.get(key), bool):
            cfg[key] = data[key]
    selected = data.get("selected_devices")
    if isinstance(selected, list):
        slots = [(v if isinstance(v, str) and len(v) < 100 else "") for v in selected[:2]]
        slots += [""] * (2 - len(slots))
        if slots[0] and slots[0] == slots[1]:
            slots[1] = ""
        cfg["selected_devices"] = slots
    labels = data.get("device_labels")
    if isinstance(labels, dict):
        cfg["device_labels"] = {k: v[:100] for k, v in labels.items()
                                if isinstance(k, str) and isinstance(v, str)}
    return cfg


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return normalize_config(data)
    except Exception:
        pass
    return normalize_config({})


def save_config(cfg: dict) -> None:
    import tempfile
    clean = normalize_config(cfg)
    pending = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=BASE_DIR,
                                         prefix=".config-", suffix=".tmp", delete=False) as f:
            pending = f.name
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(pending, CONFIG_PATH)
    except OSError:
        if pending and os.path.exists(pending):
            os.remove(pending)
        raise


def update_config(**changes) -> dict:
    cfg = load_config()
    cfg.update(changes)
    save_config(cfg)
    return normalize_config(cfg)


# --------------------------------------------------------------------------
# 开机自启
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# 开机自启：启动文件夹里的快捷方式（.lnk），不再用注册表
# --------------------------------------------------------------------------
def pythonw_path() -> str:
    exe = sys.executable or ""
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def tray_script() -> str:
    return os.path.join(BASE_DIR, "tray_app.py")


def autostart_command() -> str:
    return f'"{pythonw_path()}" "{tray_script()}"'


STARTUP_LNK_NAME = "电量托盘.lnk"

# 任务管理器/系统用这个键记录启动项的"启用/禁用"状态
#   值名 = 启动文件夹里的文件名；数据 12 字节，首字节 02=启用、03=禁用
STARTUP_APPROVED_FOLDER = (r"Software\Microsoft\Windows\CurrentVersion"
                           r"\Explorer\StartupApproved\StartupFolder")


def startup_dir() -> str:
    return win_shell.startup_dir()


def startup_lnk() -> str:
    """启动文件夹里的快捷方式路径。"""
    return os.path.join(win_shell.startup_dir(), STARTUP_LNK_NAME)


# 以前版本写在注册表里的项，启动时顺手清掉（迁移用）
LEGACY_RUN_VALUE = APP_NAME


def legacy_autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_READ) as k:
            val, _ = winreg.QueryValueEx(k, LEGACY_RUN_VALUE)
            return bool(val)
    except OSError:
        return False


def _clear_legacy_autostart() -> bool:
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, LEGACY_RUN_VALUE)
            return True
    except OSError:
        return False


def _startup_approved() -> Optional[bool]:
    """任务管理器里的启用状态：True 启用 / False 禁用 / None 没记录（默认启用）"""
    import winreg
    base = os.path.basename(startup_lnk())
    names = (base, os.path.splitext(base)[0])       # 两种命名都兼容
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_FOLDER,
                            0, winreg.KEY_READ) as k:
            for name in names:
                try:
                    data, _ = winreg.QueryValueEx(k, name)
                except OSError:
                    continue
                if isinstance(data, (bytes, bytearray)) and data:
                    return data[0] != 0x03          # 0x03 = 已禁用
    except OSError:
        pass
    return None


def _set_startup_approved(enabled: bool) -> None:
    """写启用/禁用状态——写完之后任务管理器里看到的开关状态是一致的。"""
    import winreg
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_FOLDER,
                                0, winreg.KEY_SET_VALUE) as k:
            data = bytes([0x02 if enabled else 0x03]) + bytes(11)
            winreg.SetValueEx(k, os.path.basename(startup_lnk()), 0,
                              winreg.REG_BINARY, data)
    except OSError:
        pass


def _clear_startup_approved() -> None:
    import winreg
    base = os.path.basename(startup_lnk())
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_FOLDER,
                                0, winreg.KEY_SET_VALUE) as k:
            for name in (base, os.path.splitext(base)[0]):
                try:
                    winreg.DeleteValue(k, name)
                except OSError:
                    pass
    except OSError:
        pass


def is_autostart_enabled() -> bool:
    """开机自启是否生效：快捷方式在、且没被任务管理器禁用。"""
    if win_shell.shortcut_exists(startup_lnk()):
        return _startup_approved() is not False
    return legacy_autostart_enabled()       # 兼容旧版注册表项


def set_autostart(enabled: bool) -> bool:
    """开关开机自启。

    实现 = 「启动」文件夹里的快捷方式，这样任务管理器「启动」标签里
    能直接看到它（名字就是 电量托盘），并且可以在那里直接启用/禁用。
    """
    if enabled:
        ok = win_shell.create_shortcut(
            startup_lnk(), pythonw_path(),
            arguments=f'"{tray_script()}"',
            workdir=BASE_DIR,
            description="设备电量查看器：在托盘显示主页所选设备的电量")
        _set_startup_approved(True)
    else:
        _clear_startup_approved()
        ok = win_shell.remove_shortcut(startup_lnk()) or \
            not win_shell.shortcut_exists(startup_lnk())
    _clear_legacy_autostart()               # 迁移：清掉旧的注册表项
    return ok


def autostart_status() -> dict:
    """给界面/命令行看的自启状态摘要。"""
    lnk = startup_lnk()
    approved = _startup_approved()
    return {
        "enabled": is_autostart_enabled(),
        "method": "启动文件夹快捷方式（任务管理器可见）",
        "shortcut": lnk,
        "shortcut_exists": win_shell.shortcut_exists(lnk),
        "taskmanager_state": ("已启用" if approved is not False else "已被任务管理器禁用")
                              if approved is not None else "未记录（默认启用）",
        "target": autostart_command(),
        "legacy_registry": legacy_autostart_enabled(),
    }


# --------------------------------------------------------------------------
# 桌面链接（软连接：目录 junction / 文件符号链接）
# --------------------------------------------------------------------------
DESKTOP_LINK_NAME = "设备电量查看器"          # 桌面上的项目文件夹链接名
LAUNCHER_NAMES = ["我的设备电量.bat", "电量托盘.bat"]


def desktop_dir() -> str:
    return win_shell.desktop_dir()


def desktop_link_status() -> dict:
    """桌面链接现状：{'folder': (路径, 类型), 'links': {名字: (路径, 类型)}}"""
    d = win_shell.desktop_dir()
    info = {
        "folder": (os.path.join(d, DESKTOP_LINK_NAME),
                   win_link.link_kind(os.path.join(d, DESKTOP_LINK_NAME))),
        "links": {},
    }
    for name in LAUNCHER_NAMES:
        p = os.path.join(d, name)
        info["links"][name] = (p, win_link.link_kind(p))
    return info


def install_desktop_links() -> dict:
    """建桌面链接：项目文件夹的目录链接 + 启动脚本的软链接。

    返回 {'folder': 类型, 'links': {名字: 类型}, 'errors': [消息]}
    """
    d = win_shell.desktop_dir()
    out: dict = {"folder": "", "links": {}, "errors": []}

    try:
        out["folder"] = win_link.create_dir_link(
            BASE_DIR, os.path.join(d, DESKTOP_LINK_NAME))
    except OSError as e:
        out["errors"].append(str(e))

    for name in LAUNCHER_NAMES:
        src = os.path.join(BASE_DIR, name)
        if not os.path.isfile(src):
            out["errors"].append(f"项目里没有 {name}，跳过")
            continue
        try:
            out["links"][name] = win_link.create_file_link(
                src, os.path.join(d, name))
        except OSError as e:
            out["errors"].append(str(e))
    return out


# --------------------------------------------------------------------------
# 进程间通信（窗口消息，双方都注册同名的 message）
# --------------------------------------------------------------------------
MSG_QUIT = "DeviceBatteryTray_Quit"       # 让托盘退出
MSG_RELOAD = "DeviceBatteryTray_Reload"   # 让托盘重读配置


def tray_hwnd() -> int:
    h = USER32.FindWindowW(WND_CLASS, None)
    return int(h) if h else 0


def is_tray_running() -> bool:
    return tray_hwnd() != 0


def post_to_tray(msg_name: str) -> bool:
    hwnd = tray_hwnd()
    if not hwnd:
        return False
    msg = USER32.RegisterWindowMessageW(msg_name)
    return bool(USER32.PostMessageW(hwnd, msg, 0, 0))


def ui_hwnd() -> int:
    h = USER32.FindWindowW(None, UI_TITLE)
    return int(h) if h else 0


def start_tray_app(wait_timeout: float = 6.0) -> bool:
    """启动托盘程序；返回托盘窗口是否真的起来了。

    启动前先清理"没有窗口却还占着单实例"的僵尸进程，
    启动后等托盘窗口出现（pythonw 是静默进程，只能靠窗口判断成败）。
    """
    import subprocess
    if is_tray_running():
        return True
    cleanup_zombie()
    try:
        subprocess.Popen([pythonw_path(), os.path.join(BASE_DIR, "tray_app.py")],
                         cwd=BASE_DIR, creationflags=0x00000008)
    except Exception:
        return False
    return wait_for_tray(wait_timeout)


# --------------------------------------------------------------------------
# 进程辅助：用于识别 / 清理僵尸托盘进程
# --------------------------------------------------------------------------
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
STILL_ACTIVE = 259

KERNEL32.OpenProcess.restype = ctypes.c_void_p
KERNEL32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
KERNEL32.GetExitCodeProcess.restype = wintypes.BOOL
KERNEL32.GetExitCodeProcess.argtypes = [ctypes.c_void_p,
                                        ctypes.POINTER(wintypes.DWORD)]
KERNEL32.TerminateProcess.restype = wintypes.BOOL
KERNEL32.TerminateProcess.argtypes = [ctypes.c_void_p, wintypes.UINT]
KERNEL32.CloseHandle.restype = wintypes.BOOL
KERNEL32.CloseHandle.argtypes = [ctypes.c_void_p]
KERNEL32.QueryFullProcessImageNameW.restype = wintypes.BOOL
KERNEL32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                                wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD)]

_NTDLL = ctypes.WinDLL("ntdll")
_NTDLL.NtQueryInformationProcess.restype = ctypes.c_long
_NTDLL.NtQueryInformationProcess.argtypes = [
    ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
    ctypes.POINTER(wintypes.ULONG)]
_PROCESS_COMMAND_LINE_INFORMATION = 60


class _UNICODE_STRING(ctypes.Structure):
    _fields_ = [("Length", wintypes.USHORT),
                ("MaximumLength", wintypes.USHORT),
                ("Buffer", ctypes.c_void_p)]


def process_cmdline(pid: int) -> str:
    """取进程命令行（用于确认它确实是我们的托盘程序）。"""
    h = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = 4096
        buf = ctypes.create_string_buffer(size)
        need = wintypes.ULONG(0)
        st = _NTDLL.NtQueryInformationProcess(
            h, _PROCESS_COMMAND_LINE_INFORMATION, buf, size, ctypes.byref(need))
        if st < 0 and need.value:
            size = int(need.value) + 16
            buf = ctypes.create_string_buffer(size)
            st = _NTDLL.NtQueryInformationProcess(
                h, _PROCESS_COMMAND_LINE_INFORMATION, buf, size,
                ctypes.byref(need))
        if st < 0:
            return ""
        us = _UNICODE_STRING.from_buffer(buf)
        if not us.Buffer or not us.Length:
            return ""
        return ctypes.wstring_at(us.Buffer, us.Length // 2)
    except Exception:
        return ""
    finally:
        KERNEL32.CloseHandle(h)


def write_pid(pid: Optional[int] = None) -> None:
    """把自己的 PID 写下来（进程意外卡死时，下一个实例能据此清掉它）。"""
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(pid if pid is not None else os.getpid()))
    except OSError:
        pass


def read_pid() -> int:
    try:
        with open(PID_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return 0


def remove_pid() -> None:
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    h = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD(0)
        if not KERNEL32.GetExitCodeProcess(h, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        KERNEL32.CloseHandle(h)


def process_image(pid: int) -> str:
    h = KERNEL32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if KERNEL32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        KERNEL32.CloseHandle(h)


def kill_pid(pid: int) -> bool:
    if pid <= 0:
        return False
    h = KERNEL32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not h:
        return False
    try:
        return bool(KERNEL32.TerminateProcess(h, 1))
    finally:
        KERNEL32.CloseHandle(h)


def cleanup_zombie(wait: float = 2.0) -> bool:
    """清理僵尸托盘进程：上一个实例还活着（占着单实例锁），但窗口已经销毁。

    双重确认后才动手，避免 PID 复用误杀无辜进程：
      1) 镜像名以 "python" 开头；
      2) 命令行里确实有 tray_app。
    任一不满足就只清掉自己的 pid 文件，绝不碰那个进程。
    """
    import time
    pid = read_pid()
    if pid in (0, os.getpid()):
        return False
    if not pid_alive(pid):
        remove_pid()
        return False
    image = os.path.basename(process_image(pid)).lower()
    cmd = process_cmdline(pid).lower()
    if not (image.startswith("python") and "tray_app" in cmd):
        remove_pid()
        return False
    if not kill_pid(pid):
        return False
    deadline = time.time() + max(wait, 0.2)
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.05)
    remove_pid()
    return True


def wait_for_tray(timeout: float = 6.0) -> bool:
    """等托盘窗口出现（启动是否成功只能靠它判断）。"""
    import time
    deadline = time.time() + max(timeout, 0)
    while time.time() < deadline:
        if is_tray_running():
            return True
        time.sleep(0.15)
    return is_tray_running()
