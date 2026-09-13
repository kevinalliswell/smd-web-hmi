"""跨会话进程互斥。Windows 内核对象由最后一个持有进程退出自动释放。"""

import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def single_instance(name: str, lock_path: Path):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateMutexW(None, False, "Global\\" + name)
        error = ctypes.get_last_error()
        if not handle:
            raise OSError(error, "无法取得系统级互斥锁")
        try:
            if error == 183:  # ERROR_ALREADY_EXISTS
                raise AlreadyRunning(name)
            yield handle
        finally:
            kernel.CloseHandle(handle)
    else:
        import fcntl

        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AlreadyRunning(name) from exc
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def inherited_migration_guard(handle: int):
    """Use the updater's inherited mutex without an unlocked migration interval."""
    if os.name != "nt" or not isinstance(handle, int) or handle <= 0:
        raise RuntimeError("无效的迁移锁交接")
    import ctypes
    from ctypes import wintypes

    import win32service as ws

    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("迁移锁交接仅允许 Windows 管理员")
    manager = ws.OpenSCManager(None, None, ws.SC_MANAGER_CONNECT)
    try:
        service = ws.OpenService(manager, "SmdHmi", ws.SERVICE_QUERY_STATUS)
        try:
            state = ws.QueryServiceStatusEx(service)
            if state["CurrentState"] != ws.SERVICE_STOPPED or state["ProcessId"]:
                raise RuntimeError("迁移前服务必须已完全停止")
        finally:
            ws.CloseServiceHandle(service)
    finally:
        ws.CloseServiceHandle(manager)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    compare = ctypes.WinDLL("kernelbase", use_last_error=True).CompareObjectHandles
    compare.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    compare.restype = wintypes.BOOL
    expected = kernel.OpenMutexW(0x00100000, False, "Global\\SmdHmi.Backend")
    if not expected:
        raise RuntimeError("缺少更新器持有的迁移锁")
    try:
        if not compare(handle, expected):
            raise RuntimeError("继承句柄不属于本设备后台互斥锁")
        yield
    finally:
        kernel.CloseHandle(expected)
