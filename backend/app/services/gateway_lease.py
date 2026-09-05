"""同一OS主机、同一字面IP端点的生产网关租约；不代表板端或跨主机控制权。"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import stat
import threading
from pathlib import Path

POSIX_LOCK_ROOT = Path("/var/lock/smd-hmi")
_owned_endpoints: set[tuple[int, str]] = set()
_ownership_lock = threading.Lock()


class GatewayLeaseError(RuntimeError):
    """共享租约不可用时拒绝启动第二个生产网关。"""


def endpoint_identity(host: str, port: int) -> str:
    if type(port) is not int or not 1 <= port <= 65535:
        raise GatewayLeaseError("设备端口必须在1到65535之间")
    try:
        address = ipaddress.ip_address(host.strip().removeprefix("[").removesuffix("]"))
    except (ValueError, AttributeError) as exc:
        raise GatewayLeaseError("生产设备地址必须为字面IPv4/IPv6；DNS名称不能保证稳定的独占身份") from exc
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        elif address.scope_id:
            zone = address.scope_id
            try:
                index = int(zone) if zone.isdecimal() else socket.if_nametoindex(zone)
            except (ValueError, OSError) as exc:
                raise GatewayLeaseError("IPv6接口标识无效") from exc
            return f"[{str(address).split('%')[0]}%{index}]:{port}"
    return f"[{address.compressed}]:{port}" if address.version == 6 else f"{address.compressed}:{port}"


class GatewayLease:
    """生产使用固定共享命名空间；lock_root仅供隔离测试显式注入。

    Windows mutex属于取得它的OS线程，acquire/close必须在同一个生命周期线程执行。
    POSIX部署需管理员预建root-owned /var/lock/smd-hmi(mode01777)，运行期不创建目录。
    """

    def __init__(self, host: str, port: int, *, lock_root: Path | str | None = None):
        self.endpoint = endpoint_identity(host, port)
        self.identity = hashlib.sha256(self.endpoint.encode("ascii")).hexdigest()
        self.host = (
            self.endpoint[1 : self.endpoint.rindex("]")]
            if self.endpoint.startswith("[")
            else self.endpoint.rsplit(":", 1)[0]
        )
        self._owner_key = None
        self._custom_root = lock_root is not None
        self.root = Path(lock_root) if lock_root is not None else POSIX_LOCK_ROOT
        self.lock_path = self.root / (self.identity + ".lock")
        self._fd: int | None = None
        self._handle = None
        self._kernel = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_args):
        self.close()

    def acquire(self) -> None:
        if self._fd is not None or self._handle is not None:
            raise GatewayLeaseError("此租约已经取得")
        key = (os.getpid(), self.identity)
        with _ownership_lock:
            if key in _owned_endpoints:
                raise GatewayLeaseError(f"设备{self.endpoint}已被当前进程的另一网关占用")
            _owned_endpoints.add(key)
        self._owner_key = key
        try:
            if os.name == "nt":
                self._acquire_windows()
            else:
                self._acquire_posix()
        except BaseException as exc:
            with _ownership_lock:
                _owned_endpoints.discard(key)
            self._owner_key = None
            if isinstance(exc, OSError):
                raise GatewayLeaseError(f"设备{self.endpoint}共享租约不可用: {exc}") from exc
            raise

    def _acquire_posix(self) -> None:
        import fcntl

        directory = self.root.lstat()
        if not stat.S_ISDIR(directory.st_mode) or self.root.is_symlink():
            raise GatewayLeaseError("共享锁路径必须是实际目录，不能是符号链接")
        if not self._custom_root:
            if directory.st_uid != 0 or stat.S_IMODE(directory.st_mode) != 0o1777:
                raise GatewayLeaseError("管理员须预建root-owned /var/lock/smd-hmi，权限01777")
        elif directory.st_uid != os.getuid() or directory.st_mode & 0o022:
            raise GatewayLeaseError("测试锁目录必须由当前用户拥有且禁止其他用户写入")
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        created = False
        try:
            descriptor = os.open(self.lock_path, flags | os.O_CREAT | os.O_EXCL, 0o644)
            created = True
        except FileExistsError:
            descriptor = os.open(self.lock_path, flags)
        try:
            if created:
                # 保证不同服务账户可打开同一inode；文件不存储任何敏感内容。
                os.fchmod(descriptor, 0o644)
            info = os.fstat(descriptor)
            current = self.lock_path.stat(follow_symlinks=False)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
            ):
                raise GatewayLeaseError("共享锁文件不是唯一且稳定的普通文件")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise GatewayLeaseError(f"设备{self.endpoint}已被另一网关占用") from exc
        except BaseException:
            os.close(descriptor)
            raise
        self._fd = descriptor

    def _acquire_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        class SecurityAttributes(ctypes.Structure):
            _fields_ = [("length", wintypes.DWORD), ("descriptor", ctypes.c_void_p), ("inherit", wintypes.BOOL)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        convert.restype = wintypes.BOOL
        kernel.CreateMutexExW.argtypes = [
            ctypes.POINTER(SecurityAttributes),
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        kernel.CreateMutexExW.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel.ReleaseMutex.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        descriptor = ctypes.c_void_p()
        # SYNCHRONIZE | MUTEX_MODIFY_STATE：跨登录会话共享，不赋予普通用户改DACL权限。
        sddl = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100001;;;AU)(A;;0x00100001;;;LS)(A;;0x00100001;;;NS)S:(ML;;NW;;;ME)"
        if not convert(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            security = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor.value, False)
            handle = kernel.CreateMutexExW(
                ctypes.byref(security), "Global\\SmdHmi.Gateway." + self.identity, 0, 0x00100001
            )
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(descriptor)
        outcome = kernel.WaitForSingleObject(handle, 0)
        if outcome not in {0, 0x80}:  # WAIT_ABANDONED同样取得所有权，前一进程已死亡。
            error = ctypes.get_last_error()
            kernel.CloseHandle(handle)
            if outcome == 0x102:
                raise GatewayLeaseError(f"设备{self.endpoint}已被另一网关占用")
            raise ctypes.WinError(error)
        self._kernel, self._handle = kernel, handle

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            try:
                if not self._kernel.ReleaseMutex(handle):
                    import ctypes

                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                self._kernel.CloseHandle(handle)
        if self._fd is not None:
            descriptor, self._fd = self._fd, None
            os.close(descriptor)
        with _ownership_lock:
            _owned_endpoints.discard(self._owner_key)
        self._owner_key = None
        # 永不unlink：删除并重建会让新进程锁到另一inode，破坏互斥。
