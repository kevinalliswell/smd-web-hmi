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
            yield
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
