"""Install a pinned official payload without executing the mutable bootstrapper."""
import hashlib
import io
import json
import pathlib
import platform
import re
import tarfile
import urllib.request

pins = json.loads(pathlib.Path(__file__).with_name("antigravity.json").read_text())
arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
pin = pins["platforms"][arch]
OFFICIAL_URL = re.compile(
    r"https://storage\.googleapis\.com/antigravity-public/antigravity-cli/[0-9.]+-[0-9]+/linux-(x64|arm)/cli_linux_[a-z0-9]+\.tar\.gz"
)
if not isinstance(pin.get("url"), str) or not OFFICIAL_URL.fullmatch(pin["url"]):
    raise RuntimeError("Antigravity pin url is not the official release path")
with urllib.request.urlopen(pin["url"], timeout=120) as response:
    if response.geturl() != pin["url"]:
        raise RuntimeError("Antigravity download redirected")
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
