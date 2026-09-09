#!/usr/bin/env bash
# Build a throwaway busybox rootfs and run a few containers in it.
# Linux only. Needs: busybox (static preferred) and unprivileged user namespaces.
set -euo pipefail

command -v busybox >/dev/null || { echo "install busybox first"; exit 1; }
[ "$(uname -s)" = "Linux" ] || { echo "this only works on Linux"; exit 1; }

ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT"/{bin,proc,sys,dev,tmp,etc}
cp "$(command -v busybox)" "$ROOT/bin/busybox"
for a in sh ls cat hostname ps echo id uname mount whoami; do
    ln -sf busybox "$ROOT/bin/$a"
done

TD="python -m tinydocker"

echo "### 1. hostname + PID + process isolation"
$TD run --rootfs "$ROOT" --hostname demo-box /bin/sh -c '
    echo "hostname : $(hostname)   (host is '"$(hostname)"')"
    echo "I am PID : $$"
    echo "visible processes: $(ps -o pid= | wc -l)   (host has hundreds)"
    echo "my root  : $(ls / | tr "\n" " ")"
'

echo
echo "### 2. a command that exits non-zero"
$TD run --rootfs "$ROOT" /bin/sh -c 'exit 7' || echo "exit code propagated: $?"

echo
echo "### 3. pull a real image from Docker Hub and run it"
$TD pull alpine "$ROOT-alpine"
$TD run --rootfs "$ROOT-alpine" --hostname alpine-box /bin/sh -c \
    'echo "alpine $(cat /etc/alpine-release) as PID $$"; cat /etc/os-release | head -1'
rm -rf "$ROOT-alpine"

echo
echo "### 4. resource limits (needs root or cgroup delegation; skipped otherwise)"
$TD run --rootfs "$ROOT" --memory 32M --cpus 0.5 --pids 16 /bin/sh -c \
    'cat /sys/fs/cgroup/memory.max 2>/dev/null && echo "  ^ memory cap seen inside" || echo "  (cgroup not delegated here)"'
