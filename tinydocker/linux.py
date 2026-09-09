"""Thin ``ctypes`` bindings for the Linux syscalls a container runtime needs.

Everything here is Linux-only. Importing the module works anywhere (so the
package can be inspected / unit-tested on any OS); calling the functions on a
non-Linux platform raises :class:`OSError`.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import platform
import sys

IS_LINUX = sys.platform.startswith("linux")

# -- clone/unshare flags (from <linux/sched.h>) ------------------------
CLONE_NEWNS = 0x00020000      # mount namespace
CLONE_NEWUTS = 0x04000000     # hostname / domainname
CLONE_NEWIPC = 0x08000000     # System V IPC, POSIX message queues
CLONE_NEWUSER = 0x10000000    # user + group IDs
CLONE_NEWPID = 0x20000000     # process IDs
CLONE_NEWNET = 0x40000000     # network devices, stacks, ports
CLONE_NEWCGROUP = 0x02000000  # cgroup root directory

NAMESPACE_FLAGS = {
    "mnt": CLONE_NEWNS, "uts": CLONE_NEWUTS, "ipc": CLONE_NEWIPC,
    "user": CLONE_NEWUSER, "pid": CLONE_NEWPID, "net": CLONE_NEWNET,
    "cgroup": CLONE_NEWCGROUP,
}

# -- mount flags (from <sys/mount.h>) --------------------------------
MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_BIND = 4096
MS_REC = 16384
MS_PRIVATE = 1 << 18
MS_SLAVE = 1 << 19

# -- x86-64 / arm64 syscall numbers we call directly ---------------
_SYS_pivot_root = {"x86_64": 155, "aarch64": 41}.get(platform.machine())

_libc = None


def _libc_handle():
    global _libc
    if _libc is None:
        if not IS_LINUX:
            raise OSError("Linux-only")
        _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6",
                            use_errno=True)
    return _libc


def _check(ret: int, what: str) -> None:
    if ret != 0:
        e = ctypes.get_errno()
        raise OSError(e, f"{what}: {os.strerror(e)}")


def unshare(flags: int) -> None:
    _check(_libc_handle().unshare(flags), "unshare")


def mount(source: str, target: str, fstype: str | None,
          flags: int = 0, data: str | None = None) -> None:
    ret = _libc_handle().mount(
        source.encode() if source else None,
        target.encode(),
        fstype.encode() if fstype else None,
        ctypes.c_ulong(flags),
        data.encode() if data else None,
    )
    _check(ret, f"mount({source} -> {target})")


def umount2(target: str, flags: int = 0) -> None:
    _check(_libc_handle().umount2(target.encode(), flags), f"umount2({target})")


def pivot_root(new_root: str, put_old: str) -> None:
    if _SYS_pivot_root is None:
        raise OSError(f"pivot_root syscall number unknown for {platform.machine()}")
    ret = _libc_handle().syscall(_SYS_pivot_root, new_root.encode(), put_old.encode())
    _check(ret, "pivot_root")


def sethostname(name: str) -> None:
    # socket.sethostname exists on Linux and is simplest.
    import socket
    socket.sethostname(name)


def write_id_maps(pid: int | str, uid: int, gid: int) -> None:
    """Map the caller's ``uid``/``gid`` to root inside a new user namespace."""
    base = f"/proc/{pid}"
    try:
        with open(f"{base}/setgroups", "w") as f:
            f.write("deny")
    except FileNotFoundError:
        pass
    with open(f"{base}/uid_map", "w") as f:
        f.write(f"0 {uid} 1")
    with open(f"{base}/gid_map", "w") as f:
        f.write(f"0 {gid} 1")


def parse_flags(names: list[str]) -> int:
    flags = 0
    for n in names:
        if n not in NAMESPACE_FLAGS:
            raise ValueError(f"unknown namespace {n!r}")
        flags |= NAMESPACE_FLAGS[n]
    return flags
