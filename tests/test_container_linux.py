"""Real container tests. Linux-only; each also needs unprivileged user
namespaces and a static busybox to build a throwaway rootfs."""

import os
import shutil
import subprocess
import sys
import textwrap

import pytest

from tinydocker.container import Container, ContainerError
from tinydocker.linux import IS_LINUX

pytestmark = pytest.mark.skipif(not IS_LINUX, reason="containers need Linux")

BUSYBOX = shutil.which("busybox")


def _userns_ok() -> bool:
    try:
        r = subprocess.run(["unshare", "--user", "--map-root-user", "true"],
                           capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


needs_runtime = pytest.mark.skipif(
    not (BUSYBOX and _userns_ok()),
    reason="needs busybox + unprivileged user namespaces",
)


@pytest.fixture
def rootfs(tmp_path):
    root = tmp_path / "rootfs"
    for d in ("bin", "proc", "sys", "dev", "tmp", "etc"):
        (root / d).mkdir(parents=True)
    shutil.copy(BUSYBOX, root / "bin" / "busybox")
    os.chmod(root / "bin" / "busybox", 0o755)
    for applet in ("sh", "ls", "cat", "hostname", "ps", "echo", "mount", "sleep", "id"):
        os.symlink("busybox", root / "bin" / applet)
    # if busybox is dynamically linked, copy its libs
    ldd = subprocess.run(["ldd", str(root / "bin" / "busybox")],
                         capture_output=True, text=True)
    for line in ldd.stdout.splitlines():
        parts = line.split()
        for tok in parts:
            if tok.startswith("/") and os.path.exists(tok):
                dest = root / tok.lstrip("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(tok, dest)
    return str(root)


def run_capture(rootfs, *cmd, **kw):
    """Run a container and capture stdout by pointing it at a pipe."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.dup2(w, 1)
        os.close(r)
        try:
            code = Container(list(cmd), rootfs, **kw).run()
        except BaseException:
            os._exit(99)
        os._exit(code)
    os.close(w)
    out = b""
    while chunk := os.read(r, 4096):
        out += chunk
    os.close(r)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), out.decode(errors="replace")


@needs_runtime
def test_hostname_is_isolated(rootfs):
    code, out = run_capture(rootfs, "/bin/hostname", hostname="tinybox")
    assert code == 0
    assert out.strip() == "tinybox"
    assert out.strip() != os.uname().nodename


@needs_runtime
def test_command_runs_as_pid_1(rootfs):
    code, out = run_capture(rootfs, "/bin/sh", "-c", "echo pid=$$")
    assert code == 0
    assert out.strip() == "pid=1"


@needs_runtime
def test_process_table_is_isolated(rootfs):
    # inside a fresh PID namespace with its own /proc, `ps` sees ~1-2 procs
    code, out = run_capture(rootfs, "/bin/sh", "-c", "ps -o pid,comm | wc -l")
    assert code == 0
    assert int(out.strip()) <= 4               # header + sh + ps, not the host's hundreds


@needs_runtime
def test_filesystem_is_isolated(rootfs):
    # a file on the host root is NOT visible; the container root is the rootfs
    code, out = run_capture(rootfs, "/bin/sh", "-c",
                            "ls / | tr '\\n' ' '; echo; [ -e /etc/hostname ] && echo HOSTFILE || echo NOHOSTFILE")
    assert code == 0
    assert "bin" in out and "proc" in out
    assert "NOHOSTFILE" in out                  # host's /etc/hostname not present
    assert ".pivot_old" not in out              # old root was detached


@needs_runtime
def test_exit_code_propagates(rootfs):
    code, _ = run_capture(rootfs, "/bin/sh", "-c", "exit 42")
    assert code == 42


@needs_runtime
def test_fresh_proc_mount(rootfs):
    code, out = run_capture(rootfs, "/bin/sh", "-c",
                            "cat /proc/1/comm; mount | grep -c ' /proc '")
    assert code == 0
    assert out.splitlines()[0].strip() in ("sh", "busybox")
    assert out.splitlines()[1].strip() == "1"


def test_run_on_non_linux_raises_helpfully(monkeypatch):
    monkeypatch.setattr("tinydocker.container.linux.IS_LINUX", False)
    with pytest.raises(ContainerError, match="Linux"):
        Container(["/bin/true"], "/").run()


def _cgroup_writable() -> bool:
    from tinydocker.cgroups import Cgroup
    if not Cgroup.available():
        return False
    try:
        cg = Cgroup("probe-" + os.urandom(3).hex())
        cg.create(__import__("tinydocker.cgroups", fromlist=["Limits"]).Limits(pids_max=64))
        ok = os.path.exists(os.path.join(cg.path, "pids.max"))
        cg.destroy()
        return ok
    except OSError:
        return False


@pytest.mark.skipif(not (BUSYBOX and _userns_ok()), reason="needs busybox + userns")
@pytest.mark.skipif(not _cgroup_writable(),
                    reason="no writable cgroup v2 tree (needs root or cgroup delegation)")
def test_cgroup_limits_are_applied(rootfs):
    from tinydocker.cgroups import Limits

    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.dup2(w, 1); os.close(r)
        code = Container(["/bin/sh", "-c", "cat /sys/fs/cgroup/pids.max"],
                        rootfs, limits=Limits(pids_max=17),
                        namespaces=["user", "mnt", "pid", "uts", "ipc", "net", "cgroup"]
                        ).run()
        os._exit(code)
    os.close(w)
    out = b""
    while chunk := os.read(r, 4096):
        out += chunk
    os.close(r)
    os.waitpid(pid, 0)
    assert out.strip() == b"17"
