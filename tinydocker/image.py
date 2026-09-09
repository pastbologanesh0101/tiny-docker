"""Pull an image rootfs from the Docker Hub registry, or unpack a local
``docker export`` tarball.

The registry dance (all over HTTPS, stdlib only):

1. ``GET  auth.docker.io/token?service=registry.docker.io&scope=repository:<repo>:pull``
   -> a bearer token.
2. ``GET  registry-1.docker.io/v2/<repo>/manifests/<tag>`` with that token and
   ``Accept: application/vnd.oci.image.index.v1+json, ...`` -> a manifest list;
   pick the entry for this OS/arch -> a single-image manifest listing layers.
3. ``GET  .../blobs/<digest>`` for each layer -> a gzip'd tar; extract them in
   order over the rootfs, honouring whiteout files.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import platform
import shutil
import tarfile
import urllib.request

AUTH = "https://auth.docker.io/token"
REGISTRY = "https://registry-1.docker.io"
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])


def _arch() -> str:
    m = platform.machine()
    return {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(m, m)


def _get(url: str, token: str | None = None, accept: str | None = None) -> bytes:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if accept:
        req.add_header("Accept", accept)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _normalise_repo(name: str) -> tuple[str, str]:
    """`alpine` -> `library/alpine`, `alpine:3.19` -> `(library/alpine, 3.19)`."""
    ref, _, tag = name.partition(":")
    tag = tag or "latest"
    if "/" not in ref:
        ref = "library/" + ref
    return ref, tag


def pull(name: str, dest: str, *, arch: str | None = None) -> str:
    """Download image ``name`` and extract its rootfs into ``dest``. Returns dest."""
    repo, tag = _normalise_repo(name)
    arch = arch or _arch()

    token = json.loads(_get(
        f"{AUTH}?service=registry.docker.io&scope=repository:{repo}:pull"))["token"]

    manifest = json.loads(_get(
        f"{REGISTRY}/v2/{repo}/manifests/{tag}", token, ACCEPT))

    if manifest.get("manifests"):                 # multi-arch index -> pick ours
        digest = None
        for m in manifest["manifests"]:
            p = m.get("platform", {})
            if p.get("architecture") == arch and p.get("os") == "linux":
                digest = m["digest"]
                break
        if digest is None:
            raise RuntimeError(f"no linux/{arch} image for {name}")
        manifest = json.loads(_get(
            f"{REGISTRY}/v2/{repo}/manifests/{digest}", token, ACCEPT))

    os.makedirs(dest, exist_ok=True)
    for layer in manifest["layers"]:
        blob = _get(f"{REGISTRY}/v2/{repo}/blobs/{layer['digest']}", token)
        _extract_layer(blob, dest)
    return dest


def _extract_layer(blob: bytes, dest: str) -> None:
    raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else blob
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        for member in tar.getmembers():
            name = member.name
            base = os.path.basename(name)
            target = os.path.join(dest, name)
            if base == ".wh..wh..opq":            # opaque dir: clear its contents
                d = os.path.dirname(target)
                if os.path.isdir(d):
                    for entry in os.listdir(d):
                        _rm(os.path.join(d, entry))
                continue
            if base.startswith(".wh."):            # whiteout: remove the shadowed path
                _rm(os.path.join(os.path.dirname(target), base[4:]))
                continue
            try:
                tar.extract(member, dest, filter="tar")
            except TypeError:                      # Python < 3.12 has no filter=
                tar.extract(member, dest)
            except (PermissionError, OSError):
                pass


def _rm(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


def from_export(tar_path: str, dest: str) -> str:
    """Unpack a ``docker export`` (flattened container filesystem) tarball."""
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(tar_path) as tar:
        try:
            tar.extractall(dest, filter="tar")
        except TypeError:
            tar.extractall(dest)
    return dest
