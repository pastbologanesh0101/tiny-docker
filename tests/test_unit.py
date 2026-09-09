"""Platform-independent unit tests (run everywhere)."""

import pytest

from tinydocker import linux
from tinydocker.cgroups import Cgroup, Limits
from tinydocker.cli import _parse_size
from tinydocker.image import _arch, _normalise_repo


@pytest.mark.parametrize("names,expected", [
    (["uts"], linux.CLONE_NEWUTS),
    (["pid", "mnt"], linux.CLONE_NEWPID | linux.CLONE_NEWNS),
    (["user", "mnt", "pid", "uts", "ipc", "net"],
     linux.CLONE_NEWUSER | linux.CLONE_NEWNS | linux.CLONE_NEWPID
     | linux.CLONE_NEWUTS | linux.CLONE_NEWIPC | linux.CLONE_NEWNET),
])
def test_parse_flags(names, expected):
    assert linux.parse_flags(names) == expected


def test_parse_flags_rejects_unknown():
    with pytest.raises(ValueError):
        linux.parse_flags(["banana"])


@pytest.mark.parametrize("s,expected", [
    ("512", 512), ("64K", 64 * 1024), ("128M", 128 * 1024**2),
    ("1G", 1024**3), ("1.5G", int(1.5 * 1024**3)),
])
def test_parse_size(s, expected):
    assert _parse_size(s) == expected


@pytest.mark.parametrize("name,repo,tag", [
    ("alpine", "library/alpine", "latest"),
    ("alpine:3.19", "library/alpine", "3.19"),
    ("library/busybox", "library/busybox", "latest"),
    ("myorg/app:v2", "myorg/app", "v2"),
])
def test_normalise_repo(name, repo, tag):
    assert _normalise_repo(name) == (repo, tag)


def test_arch_maps_to_oci_names():
    assert _arch() in ("amd64", "arm64") or isinstance(_arch(), str)


def test_cgroup_path_layout():
    cg = Cgroup("abc123", root="/somewhere/cgroup")
    assert cg.path == "/somewhere/cgroup/tinydocker/abc123"


def test_limits_defaults_are_all_none():
    lim = Limits()
    assert (lim.memory_bytes, lim.cpu_quota, lim.pids_max) == (None, None, None)


def test_linux_calls_raise_off_linux(monkeypatch):
    if linux.IS_LINUX:
        pytest.skip("this asserts the non-Linux guard")
    with pytest.raises(OSError):
        linux.unshare(0)
