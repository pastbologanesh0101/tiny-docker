"""``tinydocker`` command line.

    tinydocker pull alpine                 # fetch a rootfs into ./images/alpine
    tinydocker run --rootfs ./images/alpine --hostname box sh
    tinydocker run --memory 64M --cpus 0.5 --rootfs ./images/alpine \\
                   sh -c 'echo hi from $(hostname); ps aux'
    tinydocker import container.tar ./images/mybox     # from `docker export`
"""

from __future__ import annotations

import argparse
import os
import sys

from .cgroups import Limits
from .container import Container, ContainerError


def _parse_size(s: str) -> int:
    s = s.strip().upper()
    mult = {"K": 1024, "M": 1024**2, "G": 1024**3}
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(s)


def cmd_pull(ns) -> int:
    from . import image
    dest = ns.dest or os.path.join("images", ns.name.split(":")[0].split("/")[-1])
    print(f"pulling {ns.name} -> {dest}")
    image.pull(ns.name, dest)
    print(f"done: {dest}  ({_count(dest)} files)")
    return 0


def cmd_import(ns) -> int:
    from . import image
    image.from_export(ns.tarball, ns.dest)
    print(f"unpacked {ns.tarball} -> {ns.dest}  ({_count(ns.dest)} files)")
    return 0


def cmd_run(ns) -> int:
    if not ns.command:
        print("error: no command given", file=sys.stderr)
        return 2
    limits = Limits(
        memory_bytes=_parse_size(ns.memory) if ns.memory else None,
        cpu_quota=float(ns.cpus) if ns.cpus else None,
        pids_max=ns.pids,
    )
    c = Container(
        argv=ns.command,
        rootfs=os.path.abspath(ns.rootfs),
        hostname=ns.hostname,
        limits=limits,
        env=dict(kv.split("=", 1) for kv in ns.env),
        workdir=ns.workdir,
        namespaces=(["mnt", "pid", "uts", "ipc", "net"] if ns.no_userns
                    else ["user", "mnt", "pid", "uts", "ipc", "net"]),
    )
    try:
        return c.run()
    except ContainerError as e:
        print(f"tinydocker: {e}", file=sys.stderr)
        return 1


def _count(path: str) -> int:
    return sum(len(files) for _, _, files in os.walk(path))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tinydocker")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("pull")
    s.add_argument("name", help="e.g. alpine, alpine:3.19, library/busybox")
    s.add_argument("dest", nargs="?")
    s.set_defaults(func=cmd_pull)

    s = sub.add_parser("import")
    s.add_argument("tarball"); s.add_argument("dest")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("run")
    s.add_argument("--rootfs", required=True)
    s.add_argument("--hostname", default="container")
    s.add_argument("--memory", help="e.g. 64M, 512M, 1G")
    s.add_argument("--cpus", help="fraction of one CPU, e.g. 0.5 or 2")
    s.add_argument("--pids", type=int, help="max processes")
    s.add_argument("--env", "-e", action="append", default=[], metavar="K=V")
    s.add_argument("--workdir", "-w", default="/")
    s.add_argument("--no-userns", action="store_true",
                   help="don't create a user namespace (needs root)")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    ns = build_parser().parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    raise SystemExit(main())
