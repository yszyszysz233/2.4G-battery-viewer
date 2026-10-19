"""Serialize our panel/tray queries to each receiver across processes."""
import ctypes
from contextlib import contextmanager
from ctypes import wintypes

from hid_win import HidError

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


@contextmanager
def device_lock(identity, timeout_ms=5000):
    mutex = kernel32.CreateMutexW(None, False, f"Local\\DeviceBatteryHidpp_{identity.upper()}")
    if not mutex:
        raise HidError("无法创建设备读取锁")
    acquired = False
    try:
        acquired = kernel32.WaitForSingleObject(mutex, timeout_ms) in (0, 0x80)
        if not acquired:
            raise HidError("设备正在读取中，请稍后刷新")
        yield
    finally:
        if acquired:
            kernel32.ReleaseMutex(mutex)
        kernel32.CloseHandle(mutex)
