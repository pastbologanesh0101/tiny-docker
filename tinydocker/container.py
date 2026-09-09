"""Create and run a container: new namespaces, an isolated root filesystem, a
fresh ``/proc``, an optional cgroup, and the command as PID 1.

Flow (all unprivileged, via a user namespace):

    parent: unshare(USER|MNT|PID|UTS|IPC|NET)
            write /proc/self/uid_map, gid_map   (map caller -> root inside)
            fork()  --------------------------------------------┐
    child (PID 1 in the new PID namespace):                     │
            join the cgroup                                     │
            make mounts private; bind-mount rootfs onto itself  │
            pivot_root into it; mount /proc; drop the old root  │
            sethostname(); execvpe(cmd)                         │
    parent: waitpid(child); tear down the cgroup  <-------------┘
"""

from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass, field

from . import linux
from .cgroups import Cgroup, Limits

MNT_DETACH = 2
DEFAULT_NS = ["user", "mnt", "pid", "uts", "ipc", "net"]
DEFAULT_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "TERM": "xterm",
    "HOME": "/root",
}


class ContainerError(RuntimeError):
    pass


@dataclass
class Container:
    argv: list[str]
    rootfs: str
    hostname: str = "container"
    namespaces: list[str] = field(default_factory=lambda: list(DEFAULT_NS))
    limits: Limits = field(default_factory=Limits)
    env: dict = field(default_factory=dict)
    workdir: str = "/"

    def run(self) -> int:
        if not linux.IS_LINUX:
            raise ContainerError(
                "containers need Linux namespaces; run this on Linux "
                "(a VM or `docker run` an interpreter) — see the README")
        if not os.path.isdir(self.rootfs):
            raise ContainerError(f"rootfs {self.rootfs!r} is not a directory")

        cg = None
        if any(v is not None for v in vars(self.limits).values()):
            if Cgroup.available():
                cg = Cgroup("c-" + secrets.token_hex(4))
                try:
                    cg.create(self.limits)
                except OSError as e:            # e.g. no cgroup delegation in CI
                    sys.stderr.write(f"tinydocker: cgroup limits skipped ({e})\n")
                    cg = None
            else:
                sys.stderr.write("tinydocker: cgroup v2 not available; limits skipped\n")

        uid, gid = os.getuid(), os.getgid()
        try:
            linux.unshare(linux.parse_flags(self.namespaces))
        except OSError as e:
            raise ContainerError(
                f"unshare failed ({e}). Unprivileged user namespaces may be "
                f"disabled (sysctl kernel.unprivileged_userns_clone).") from e

        if "user" in self.namespaces:
            linux.write_id_maps("self", uid, gid)

        pid = os.fork()
        if pid == 0:
            self._child(cg)
            os._exit(127)                       # execvpe failed if we get here

        try:
            _, status = os.waitpid(pid, 0)
        finally:
            if cg:
                cg.destroy()
        return os.waitstatus_to_exitcode(status)

    # -- the container's PID 1 -------------------------------
    def _child(self, cg: Cgroup | None) -> None:
        try:
            if cg:
                try:
                    cg.add_process(os.getpid())
                except OSError:
                    pass

            L = linux
            # 1. don't let our mount changes propagate back to the host
            L.mount("", "/", None, L.MS_REC | L.MS_PRIVATE)
            # 2. pivot_root requires new_root to be a mount point
            L.mount(self.rootfs, self.rootfs, None, L.MS_BIND | L.MS_REC)

            old = os.path.join(self.rootfs, ".pivot_old")
            os.makedirs(old, exist_ok=True)
            os.chdir(self.rootfs)
            L.pivot_root(".", ".pivot_old")
            os.chdir("/")

            # 3. a private /proc for this PID namespace
            os.makedirs("/proc", exist_ok=True)
            L.mount("proc", "/proc", "proc",
                    L.MS_NOSUID | L.MS_NODEV | L.MS_NOEXEC)
            for fs, path in (("sysfs", "/sys"), ("tmpfs", "/dev"), ("tmpfs", "/tmp")):
                try:
                    os.makedirs(path, exist_ok=True)
                    L.mount(fs, path, fs if fs != "tmpfs" else "tmpfs",
                            L.MS_NOSUID | (L.MS_NODEV if fs != "tmpfs" else 0))
                except OSError:
                    pass
            self._minimal_dev()

            # 4. drop the old root
            try:
                L.umount2("/.pivot_old", MNT_DETACH)
                os.rmdir("/.pivot_old")
            except OSError:
                pass

            L.sethostname(self.hostname)

            env = {**DEFAULT_ENV, **self.env}
            try:
                os.chdir(self.workdir)
            except OSError:
                os.chdir("/")

            os.execvpe(self.argv[0], self.argv, env)
        except Exception as e:                  # pragma: no cover - child only
            sys.stderr.write(f"tinydocker: container setup failed: {e}\n")
            os._exit(126)

    @staticmethod
    def _minimal_dev() -> None:
        # We can't mknod without privileges; bind the host's device nodes that
        # were still visible under the old root. Best-effort.
        for dev in ("null", "zero", "full", "random", "urandom", "tty"):
            src = f"/.pivot_old/dev/{dev}"
            dst = f"/dev/{dev}"
            try:
                open(dst, "w").close()
                linux.mount(src, dst, None, linux.MS_BIND)
            except OSError:
                pass
        for i, name in enumerate(("stdin", "stdout", "stderr")):
            try:
                os.symlink(f"/proc/self/fd/{i}", f"/dev/{name}")
            except OSError:
                pass
