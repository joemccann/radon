#!/usr/bin/env python3
"""Promote only CI-pinned operator CLIs exported from the exact gateway image.

Root-owned control-plane code; no application imports, vendor installers,
provider authentication, user-selected destination, or shell interpolation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path("/opt/radon-model-clients")
EXPORT_ROOT = Path("/var/lib/radon-model-export")
MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 100_000
EXPORT_NAME = "radon-model-client-export"
HELPERS = ("__init__.py", "client.py", "cli.py", "cli_bridge.py")
PACKAGES = {"claude": "@anthropic-ai/claude-code", "codex": "@openai/codex", "grok": "@xai-official/grok"}


def _trusted_root(root: Path, owner_uid: int) -> None:
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("non-canonical installation root")
    for parent in reversed(root.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, owner_uid):
            raise ValueError("untrusted installation ancestor")
        if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise ValueError("writable installation ancestor")
    root.mkdir(mode=0o755, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid or info.st_mode & 0o022:
        raise ValueError("untrusted installation root")


def _immutable_tree(root: Path, owner_uid: int) -> None:
    """Verify internal ancestors before trusting an existing immutable receipt."""
    for folder, dirs, files in os.walk(root, followlinks=False):
        parent=Path(folder);info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner_uid or info.st_mode&0o222:
            raise ValueError("untrusted existing client directory")
        for name in dirs:
            info=(parent/name).lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner_uid or info.st_mode&0o222:
                raise ValueError("untrusted existing client ancestor")
        for name in files:
            path=parent/name;info=path.lstat()
            if info.st_uid!=owner_uid:raise ValueError("untrusted existing client owner")
            if stat.S_ISLNK(info.st_mode):
                resolved=path.resolve(strict=True)
                if not resolved.is_relative_to(root):raise ValueError("existing client link escapes release")
                info=resolved.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=owner_uid or info.st_mode&0o222:
                raise ValueError("untrusted existing client file")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _remove_stage(path: Path) -> None:
    if not path.exists():
        return
    for folder, _dirs, _files in os.walk(path, followlinks=False):
        os.chmod(folder, 0o700)
    shutil.rmtree(path)


def _activate(root: Path, release: str) -> None:
    current = root / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("current client selector is not a symlink")
    if current.is_symlink():
        previous=os.readlink(current)
        if not re.fullmatch(r"[0-9a-f]{40}",previous):raise ValueError("invalid previous client selector")
        if previous!=release:
            old=root/previous
            _immutable_tree(old,root.stat().st_uid)
            # Native sessions may still call the old auth helper. Start their
            # retention window at demotion, not at the original build date.
            os.utime(old,None)
    temporary = root / (".current-" + os.urandom(8).hex())
    try:
        temporary.symlink_to(release, target_is_directory=True)
        os.replace(temporary, current)
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _prune_releases(root: Path, release: str, owner_uid: int) -> None:
    candidates=[]
    for entry in root.iterdir():
        if re.fullmatch(r"[0-9a-f]{40}",entry.name) and entry.name!=release:
            if entry.is_symlink() or not entry.is_dir():raise ValueError("unsafe stale client release")
            candidates.append(entry)
    cutoff=time.time()-7200
    for stale in sorted(candidates,key=lambda item:item.stat().st_mtime,reverse=True)[4:]:
        if stale.stat().st_mtime>=cutoff:continue
        _immutable_tree(stale,owner_uid)
        _remove_stage(stale)


def install_from_tar(stream, root: Path, release: str, *, owner_uid: int = 0) -> Path:
    """Validate a bounded payload completely before selecting the new release."""
    if not isinstance(release, str) or not re.fullmatch(r"[0-9a-f]{40}", release):
        raise ValueError("exact release SHA required")
    root = Path(root)
    _trusted_root(root, owner_uid)
    stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=root))
    target = root / release
    try:
        size = count = 0
        with tarfile.open(fileobj=stream, mode="r|") as archive:
            for member in archive:
                count += 1
                size += max(0, member.size)
                name = Path(member.name)
                if count > MAX_MEMBERS or size > MAX_BYTES:
                    raise ValueError("client archive exceeds bounds")
                if name.is_absolute() or ".." in name.parts or not (member.isdir() or member.isfile() or member.issym()):
                    raise ValueError("unsafe client archive member")
                if name.parts and name.parts[0] == "model_gateway" and name.as_posix() not in {"model_gateway", *("model_gateway/" + file for file in HELPERS)}:
                    raise ValueError("native gateway code is forbidden on client hosts")
                if name.parts and name.parts[0] == "lib":
                    raise ValueError("reserved client helper directory")
                if name.as_posix() == "manifest.json":
                    raise ValueError("reserved client manifest")
                if member.issym():
                    if os.path.isabs(member.linkname):
                        raise ValueError("absolute client archive link")
                    resolved = (stage / name.parent / member.linkname).resolve()
                    if not resolved.is_relative_to(stage):
                        raise ValueError("client archive link escapes release")
                archive.extract(member, stage, filter="data")
        package = json.loads((stage / "package.json").read_text())
        versions = {name: package["dependencies"][package_name] for name, package_name in PACKAGES.items()}
        versions["agy"] = json.loads((stage / "antigravity.json").read_text())["version"]
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) for value in versions.values()):
            raise ValueError("client package version is not pinned")
        node = stage / "node"
        if not node.is_file() or node.is_symlink():
            raise ValueError("pinned Node payload missing")
        os.replace(node, stage / "bin/node")
        helper_hashes = {}
        for name in HELPERS:
            source = stage / "model_gateway" / name
            if not source.is_file() or source.is_symlink():
                raise ValueError("missing trusted client helper")
            helper_hashes["model_gateway/" + name] = _digest(source)
        (stage / "lib").mkdir()
        os.rename(stage / "model_gateway", stage / "lib/model_gateway")
        hashes = {}
        for name in (*PACKAGES, "agy", "node"):
            path = (stage / "bin" / name).resolve()
            if not path.is_relative_to(stage) or not path.is_file():
                raise ValueError("missing or escaping client executable")
            hashes[name] = _digest(path)
        proof = {"version": 1, "release_sha": release, "cli_versions": versions, "sha256": hashes, "helper_sha256": helper_hashes}
        manifest = stage / "manifest.json"
        with manifest.open("w") as output:
            json.dump(proof, output, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        for folder, _dirs, files in os.walk(stage, followlinks=False):
            for name in files:
                path = Path(folder, name)
                if not path.is_symlink():
                    mode = path.stat().st_mode
                    os.chmod(path, 0o555 if mode & 0o111 else 0o444)
            os.chmod(folder, 0o555)
        if target.exists() or target.is_symlink():
            info = target.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid or info.st_mode & 0o222:
                raise ValueError("untrusted existing client release")
            _immutable_tree(target, owner_uid)
            if json.loads((target / "manifest.json").read_text()) != proof:
                raise ValueError("existing release differs from CI payload")
            for name, digest in hashes.items():
                if _digest(target / "bin" / name) != digest:
                    raise ValueError("existing client payload changed")
            for name, digest in helper_hashes.items():
                if _digest(target / "lib" / name) != digest:
                    raise ValueError("existing client helper changed")
        else:
            os.rename(stage, target)
        _activate(root, release)
        _prune_releases(root, release, owner_uid)
        return target
    finally:
        _remove_stage(stage)


def export_from_image(engine: str, image: str, target, *, darwin: bool = False) -> None:
    """An isolated unprivileged container exports bytes; nothing runs on root."""
    command = [engine, "run", "--rm", "--read-only", "--name", EXPORT_NAME, "--pull=never", "--network", "none", "--user", "1000:1000", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--entrypoint", "tar", image, "--sort=name", "--mtime=@0", "--owner=0", "--group=0", "--numeric-owner", "-cf", "-"]
    if darwin:
        command += ["-C", "/opt/radon-darwin", "."]
    else:
        command += ["-C", "/opt/radon-clis", ".", "-C", "/usr/local/bin", "node", "-C", "/home/radon/radon/scripts", *("model_gateway/" + name for name in HELPERS)]
    subprocess.run([engine, "rm", "-f", EXPORT_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    # Export is preparation, never the systemd service's main process. Podman
    # otherwise adopts the inherited socket and publishes its conmon MAINPID.
    export_env = os.environ.copy()
    export_env.pop("NOTIFY_SOCKET", None)
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=export_env)
    deadline = time.monotonic() + 60
    received = 0
    try:
        assert process.stdout is not None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("client export deadline")
            if not select.select([process.stdout], [], [], min(remaining, 1))[0]:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_BYTES:
                raise ValueError("client export exceeds bounds")
            target.write(chunk)
        if process.wait(timeout=5) != 0:
            raise ValueError("client export failed")
        target.flush()
        target.seek(0)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if process.stdout:
            process.stdout.close()
        subprocess.run([engine, "rm", "-f", EXPORT_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)


DARWIN_FILES = {"package.json", *("bin/"+name for name in (*PACKAGES,"rg")), *("model_gateway/"+name for name in HELPERS)}


def _darwin_payload(stream):
    files = {}
    package = None
    total = count = 0
    stream.seek(0)
    with tarfile.open(fileobj=stream,mode="r|") as archive:
        for member in archive:
            count += 1
            if count > 32: raise ValueError("Darwin client archive exceeds bounds")
            name = Path(member.name).as_posix().removeprefix("./")
            if member.isdir() and name in {".","bin","model_gateway"}:
                continue
            if name not in DARWIN_FILES or name in files or not member.isfile():
                raise ValueError("Darwin archive contains an unrecognized member")
            total += member.size
            if count > 32 or total > MAX_BYTES or member.size <= 0:
                raise ValueError("Darwin client archive exceeds bounds")
            source = archive.extractfile(member)
            if source is None: raise ValueError("Darwin client payload is missing")
            digest = hashlib.sha256()
            data = bytearray()
            while raw := source.read(65536):
                digest.update(raw)
                if name == "package.json":
                    if len(data)+len(raw) > 65536: raise ValueError("Darwin package metadata exceeds bounds")
                    data.extend(raw)
            files[name] = {"sha256":digest.hexdigest(),"size":member.size}
            if name == "package.json": package=json.loads(data)
    if set(files) != DARWIN_FILES or not isinstance(package,dict):
        raise ValueError("Darwin archive is incomplete")
    versions={name:package["dependencies"][provider] for name,provider in PACKAGES.items()}
    if any(not isinstance(value,str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",value) for value in versions.values()):
        raise ValueError("Darwin CLI versions must be pinned")
    return files,versions


def publish_darwin_bundle(stream, root: Path, release: str, *, owner_uid: int = 0) -> Path:
    if not isinstance(release,str) or not re.fullmatch(r"[0-9a-f]{40}",release):
        raise ValueError("exact Darwin release SHA required")
    root=Path(root);_trusted_root(root,owner_uid)
    files,versions=_darwin_payload(stream)
    stream.seek(0)
    stage=Path(tempfile.mkdtemp(prefix=".darwin-",dir=root));target=root/release
    try:
        bundle=stage/"bundle.tar"
        received=0
        with bundle.open("xb") as output:
            while raw:=stream.read(65536):
                received+=len(raw)
                if received>MAX_BYTES:raise ValueError("Darwin export exceeds bounds")
                output.write(raw)
            output.flush();os.fsync(output.fileno())
        proof={"version":1,"platform":"darwin-arm64","release_sha":release,"sha256":_digest(bundle),"size":received,"files":files,"cli_versions":versions}
        manifest=stage/"manifest.json"
        with manifest.open("x") as output:
            json.dump(proof,output,sort_keys=True);output.write("\n");output.flush();os.fsync(output.fileno())
        bundle.chmod(0o444);manifest.chmod(0o444);stage.chmod(0o555)
        if target.exists() or target.is_symlink():
            info=target.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=owner_uid or info.st_mode&0o222:
                raise ValueError("unsafe existing Darwin export")
            if json.loads((target/"manifest.json").read_text())!=proof or _digest(target/"bundle.tar")!=proof["sha256"]:
                raise ValueError("Darwin release differs from CI payload")
        else:
            os.rename(stage,target)
        return target
    finally:
        _remove_stage(stage)


def activate_darwin_export(root: Path, release: str, *, owner_uid: int = 0) -> None:
    root=Path(root);_trusted_root(root,owner_uid)
    if not isinstance(release,str) or not re.fullmatch(r"[0-9a-f]{40}",release):raise ValueError("invalid Darwin release")
    folder=root/release
    for path in (folder,folder/"manifest.json",folder/"bundle.tar"):
        info=path.lstat()
        if info.st_uid!=owner_uid or stat.S_ISLNK(info.st_mode) or info.st_mode&0o222:
            raise ValueError("Darwin export is not immutable and root-owned")
    proof=json.loads((folder/"manifest.json").read_text())
    if proof.get("release_sha")!=release or proof.get("platform")!="darwin-arm64" or proof.get("sha256")!=_digest(folder/"bundle.tar") or proof.get("size")!=(folder/"bundle.tar").stat().st_size:
        raise ValueError("Darwin export proof mismatch")
    _activate(root,release)
    candidates=[]
    for entry in root.iterdir():
        if re.fullmatch(r"[0-9a-f]{40}",entry.name) and entry.name!=release and entry.is_dir() and not entry.is_symlink():
            info=entry.lstat()
            if info.st_uid!=owner_uid or info.st_mode&0o222:raise ValueError("unsafe stale Darwin export")
            candidates.append(entry)
    for stale in sorted(candidates,key=lambda item:item.stat().st_mtime,reverse=True)[4:]:
        if stale.stat().st_mtime>=time.time()-7200:continue
        _immutable_tree(stale,owner_uid)
        _remove_stage(stale)


def main(args: list[str]) -> int:
    if os.geteuid() != 0:
        return 64
    if len(args)==2 and args[0]=="commit":
        try:
            activate_darwin_export(EXPORT_ROOT/"releases",args[1])
        except Exception as exc:
            print("Darwin export activation failed: "+type(exc).__name__,file=sys.stderr)
            return 78
        return 0
    if len(args) != 3:
        return 64
    engine, image, release = args
    if engine not in ("/usr/bin/podman", "/usr/bin/docker") or image != "ghcr.io/joemccann/radon-model-gateway:" + release or not re.fullmatch(r"[0-9a-f]{40}", release):
        return 64
    try:
        _trusted_root(ROOT, 0)
        with tempfile.TemporaryFile(dir=ROOT) as stream:
            export_from_image(engine, image, stream)
            install_from_tar(stream, ROOT, release)
        _trusted_root(EXPORT_ROOT,0)
        with tempfile.TemporaryFile(dir=EXPORT_ROOT) as stream:
            export_from_image(engine,image,stream,darwin=True)
            publish_darwin_bundle(stream,EXPORT_ROOT/"releases",release)
    except Exception as exc:
        print(f"model client promotion failed: {type(exc).__name__}", file=sys.stderr)
        return 78
    print("model clients promoted from exact CI image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
