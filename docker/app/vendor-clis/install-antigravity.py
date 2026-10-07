"""Install a pinned official payload without executing the mutable bootstrapper."""
import hashlib
import io
import json
import pathlib
import platform
import tarfile
import urllib.request

pins = json.loads(pathlib.Path(__file__).with_name("antigravity.json").read_text())
arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
pin = pins["platforms"][arch]
with urllib.request.urlopen(pin["url"], timeout=120) as response:
    payload = response.read()
if hashlib.sha512(payload).hexdigest() != pin["sha512"]:
    raise RuntimeError("Antigravity release checksum mismatch")
with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
    member = archive.getmember("antigravity")
    if not member.isfile():
        raise RuntimeError("Antigravity payload is not a regular file")
    with archive.extractfile(member) as binary:
        destination = pathlib.Path("/opt/radon-clis/bin/agy")
        destination.write_bytes(binary.read())
        destination.chmod(0o755)
