# Tiny Docker

A container runtime in ~500 lines of pure Python (stdlib only). It does what
`docker run` does at the kernel level: create **Linux namespaces**, pivot into
an isolated **root filesystem**, mount a private `/proc`, apply **cgroup v2**
resource limits, and run your command as **PID 1**. It also pulls image
rootfs's straight from the **Docker Hub registry** over HTTPS.

> **Linux only.** Namespaces, `pivot_root` and cgroups are Linux kernel
> features — this cannot run on macOS or Windows directly. On a Mac, use the
> bundled `examples/Dockerfile.tryit` to run it inside a Linux container (see
> below). The package *imports* fine anywhere so the code and unit tests are
> inspectable on any OS.

## What a container actually is (as done here)

```
parent process
  ├─ unshare(CLONE_NEWUSER | NEWNS | NEWPID | NEWUTS | NEWIPC | NEWNET)
  ├─ write /proc/self/{uid_map,gid_map}      # map your uid -> root *inside*
  └─ fork() ─────────────────────────────────────────────┐
                                                         │
  child = PID 1 in a brand-new PID namespace:            │
    ├─ join the cgroup (memory.max / cpu.max / pids.max) │
    ├─ mount("", "/", MS_REC|MS_PRIVATE)                 │  no mount leaks to host
    ├─ mount(rootfs, rootfs, MS_BIND|MS_REC)             │  make it a mount point
    ├─ pivot_root(rootfs, rootfs/.pivot_old)             │  swap the root
    ├─ mount("proc", "/proc", "proc")                    │  its own process view
    ├─ umount2("/.pivot_old", MNT_DETACH)                │  drop the host root
    ├─ sethostname("container")                          │
    └─ execvpe(argv)                                     │
  parent: waitpid(child); destroy the cgroup  <──────────┘
```

Everything runs **unprivileged** via a user namespace — no `sudo` needed for
the namespace and filesystem isolation. (cgroup limits need root or a delegated
cgroup subtree; without them Tiny Docker prints a note and continues.)

## Quick start (on Linux)

```bash
git clone https://github.com/pastbologanesh0101/tiny-docker
cd tiny-docker
pip install -e ".[dev]"
pytest -q

# pull a real image and run a shell in it
tinydocker pull alpine ./images/alpine
tinydocker run --rootfs ./images/alpine --hostname box /bin/sh

# resource limits + a one-shot command
tinydocker run --rootfs ./images/alpine --memory 64M --cpus 0.5 --pids 32 \
    /bin/sh -c 'echo "hi from $(hostname) as PID $$"; ps aux; free -m'

# or unpack a `docker export` tarball
docker export $(docker create alpine) > alpine.tar
tinydocker import alpine.tar ./images/alpine2
```

```
$ tinydocker run --rootfs ./images/alpine --hostname demo /bin/sh -c 'hostname; echo pid=$$; ls /'
demo
pid=1
bin  dev  etc  home  lib  media  mnt  opt  proc  root  run  sbin  srv  sys  tmp  usr  var
```

### On a Mac

```bash
docker build -f examples/Dockerfile.tryit -t tinydocker-tryit .
docker run --rm -it --privileged tinydocker-tryit           # runs examples/demo.sh
docker run --rm --privileged tinydocker-tryit pytest -q     # the full suite
```

## Concepts demonstrated

| Area | Where | What it shows |
|---|---|---|
| **Namespaces** | `tinydocker/linux.py`, `container.py` | `unshare(2)` with the `CLONE_NEW*` flags for UTS (hostname), PID (process tree), MNT (mounts), IPC, NET, and USER. A fresh PID namespace means the command is PID 1 and `ps` sees only container processes. |
| **User namespaces** | `linux.write_id_maps`, `container.py` | Unprivileged containers: `unshare(CLONE_NEWUSER)`, then `setgroups=deny` + `uid_map`/`gid_map` map your real uid to root inside — you get `CAP_SYS_ADMIN` in the new namespaces without being root on the host. |
| **Root filesystem** | `container.py` | `MS_REC\|MS_PRIVATE` on `/` so nothing leaks back to the host, a bind-mount to make the rootfs a mount point, `pivot_root(2)`, then `umount2(MNT_DETACH)` of the old root — the container genuinely cannot see the host filesystem. |
| **`/proc`, `/dev`, `/sys`** | `container.py` | A private `procfs` for the new PID namespace; best-effort `tmpfs` `/dev` + `/tmp` and bind-mounted device nodes; `/dev/std{in,out,err}` symlinks. |
| **cgroups v2** | `tinydocker/cgroups.py` | The unified hierarchy: create a group directory, enable controllers via `cgroup.subtree_control`, write `memory.max` / `cpu.max` / `pids.max`, attach by writing the pid to `cgroup.procs`, and `cgroup.kill` + `rmdir` on teardown. |
| **The OCI image format** | `tinydocker/image.py` | The registry pull: token auth against `auth.docker.io`, a multi-arch manifest index → per-arch manifest, then gzip'd tar **layers** extracted in order over the rootfs, honouring `.wh.` **whiteout** files. All with `urllib` + `tarfile` + `gzip`. |
| **PID 1 semantics** | `container.py` | The runtime `waitpid`s the container's init and propagates its exit code (`waitstatus_to_exitcode`). |

## Verified

The unit tests run on any OS. The container tests (Linux + user namespaces +
`busybox`) assert, against a real spawned container:

- the hostname inside differs from the host's;
- the command is PID 1;
- the process table shows ~1–2 processes, not the host's hundreds;
- `/` is the rootfs and the host's files are gone, `.pivot_old` is detached;
- exit codes propagate;
- `/proc` is a fresh mount for the namespace;
- (where the cgroup tree is writable) `pids.max` is visible inside at the set value.

## Limitations

- **No networking setup** beyond a fresh, empty net namespace — no veth pair,
  no bridge, no NAT; the container has only a down `lo`. (Real CNI plumbing is a
  project of its own.)
- **cgroup limits need privilege or delegation.** Under a bare user namespace
  with no delegated cgroup subtree, limits are skipped with a warning.
- No image *config* handling — entrypoint/cmd/env/workdir from the image
  metadata are ignored; you pass the command explicitly.
- No layered/overlay storage — layers are flattened into one directory.
- No `exec` into a running container, no daemon, no restart policies.
- Device nodes are bind-mounted best-effort (can't `mknod` unprivileged).

## Project layout

```
tinydocker/
  linux.py      ctypes bindings: unshare / mount / umount2 / pivot_root, flags
  cgroups.py    cgroup v2: create / set memory,cpu,pids / attach / destroy
  image.py      Docker Hub registry pull (token, manifest, layers, whiteouts)
  container.py  Container: unshare -> fork -> rootfs+/proc -> cgroup -> exec
  cli.py        tinydocker pull / import / run
examples/
  demo.sh              build a busybox rootfs and run several containers
  Dockerfile.tryit     run it all inside Linux from a Mac/Windows host
tests/
  test_unit.py            cross-platform (flags, size parsing, repo names, paths)
  test_container_linux.py  real containers: isolation, PID 1, /proc, cgroups
```

## License

MIT
