"""Minimal cgroup v2 support: create a group, set memory / CPU / pids limits,
attach a process, and clean up.

cgroup v2 is a single unified hierarchy mounted at ``/sys/fs/cgroup``. A group
is just a directory; limits are files inside it; a process joins by having its
pid written to ``cgroup.procs``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

CGROUP_ROOT = "/sys/fs/cgroup"


@dataclass
class Limits:
    memory_bytes: int | None = None       # memory.max
    cpu_quota: float | None = None        # fraction of one CPU, e.g. 0.5 or 2.0
    pids_max: int | None = None           # pids.max


class Cgroup:
    def __init__(self, name: str, root: str = CGROUP_ROOT):
        self.name = name
        self.root = root
        self.path = os.path.join(root, "tinydocker", name)

    # -- introspection -------------------------------------
    @staticmethod
    def available(root: str = CGROUP_ROOT) -> bool:
        return os.path.isfile(os.path.join(root, "cgroup.controllers"))

    def controllers(self) -> set[str]:
        try:
            with open(os.path.join(self.root, "cgroup.controllers")) as f:
                return set(f.read().split())
        except OSError:
            return set()

    # -- lifecycle --------------------------------------
    def create(self, limits: Limits) -> None:
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        self._enable_controllers(parent)
        os.makedirs(self.path, exist_ok=True)
        self.apply(limits)

    def _enable_controllers(self, parent: str) -> None:
        want = [c for c in ("memory", "cpu", "pids") if c in self.controllers()]
        try:
            with open(os.path.join(parent, "cgroup.subtree_control"), "w") as f:
                f.write(" ".join(f"+{c}" for c in want))
        except OSError:
            pass  # already enabled, or not permitted; apply() will surface real issues

    def apply(self, limits: Limits) -> None:
        if limits.memory_bytes is not None:
            self._write("memory.max", str(limits.memory_bytes))
            self._write("memory.swap.max", "0")
        if limits.cpu_quota is not None:
            period = 100_000
            quota = max(1000, int(limits.cpu_quota * period))
            self._write("cpu.max", f"{quota} {period}")
        if limits.pids_max is not None:
            self._write("pids.max", str(limits.pids_max))

    def add_process(self, pid: int) -> None:
        self._write("cgroup.procs", str(pid))

    def stats(self) -> dict:
        out = {}
        for key in ("memory.current", "memory.peak", "pids.current"):
            try:
                with open(os.path.join(self.path, key)) as f:
                    out[key] = f.read().strip()
            except OSError:
                pass
        return out

    def destroy(self) -> None:
        # kill anything left, then rmdir (must be empty of processes + children)
        try:
            self._write("cgroup.kill", "1")
        except OSError:
            pass
        try:
            os.rmdir(self.path)
        except OSError:
            pass

    def _write(self, name: str, value: str) -> None:
        with open(os.path.join(self.path, name), "w") as f:
            f.write(value)
