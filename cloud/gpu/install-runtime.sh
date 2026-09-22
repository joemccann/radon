#!/usr/bin/env bash
# Run only from a root-owned release staging directory on the GPU host.
set -euo pipefail
if [[ "${1:-}" == --help ]]; then
  echo 'Usage: sudo bash /root/<tested-release>/cloud/gpu/install-runtime.sh'
  exit 0
fi
[[ $# == 0 && $EUID == 0 ]] || { echo 'Run as root without arguments' >&2; exit 64; }
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
# Validate all sources and target directories before installing privileged files.
python3 - "$source_dir" <<'PY'
import pathlib
import stat
import sys

source = pathlib.Path(sys.argv[1])
for path in [source / name for name in ("runtime.py", "pins.json", "slm.json.example", "radon-slm.service")]:
    for entry in (path, *path.parents):
        st = entry.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise SystemExit(f"Refusing untrusted release source: {entry}")
    if not path.is_file():
        raise SystemExit(f"Missing release artifact: {path}")
for path in map(pathlib.Path, ("/etc/radon", "/usr/local/sbin", "/etc/systemd/system", "/var/lib/radon/slm")):
    for entry in (path, *path.parents):
        if entry.exists() or entry.is_symlink():
            st = entry.lstat()
            if stat.S_ISLNK(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
                raise SystemExit(f"Refusing untrusted destination: {entry}")
marker = pathlib.Path("/etc/radon/gpu-host")
if marker.is_symlink() or not marker.is_file() or marker.read_text().strip() != "radon-slm":
    raise SystemExit("Run the dedicated GPU bootstrap first")
info = marker.stat()
if info.st_uid != 0 or info.st_mode & 0o022:
    raise SystemExit("Refusing untrusted GPU host marker")
for path in map(pathlib.Path, ("/etc/radon/slm.json", "/etc/radon/slm-pins.json", "/etc/radon/slm-secrets.env", "/usr/local/sbin/radon-slm-runtime", "/etc/systemd/system/radon-slm.service")):
    if path.exists() or path.is_symlink():
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o022:
            raise SystemExit(f"Refusing untrusted destination file: {path}")
PY
id radon >/dev/null
install -d -o root -g radon -m 0750 /var/lib/radon/slm
# Reject mount-target symlinks before chown; the radon account cannot replace
# these directory entries because their parent remains root-owned.
for name in cache adapters; do
  [[ ! -L "/var/lib/radon/slm/$name" ]] || { echo "Refusing symlink: $name" >&2; exit 78; }
done
install -d -o radon -g radon -m 0750 /var/lib/radon/slm/cache
install -d -o root -g radon -m 0750 /var/lib/radon/slm/adapters
install -o root -g root -m 0755 "$source_dir/runtime.py" /usr/local/sbin/radon-slm-runtime
install -o root -g root -m 0644 "$source_dir/pins.json" /etc/radon/slm-pins.json
install -o root -g root -m 0644 "$source_dir/radon-slm.service" /etc/systemd/system/radon-slm.service
if [[ ! -e /etc/radon/slm.json ]]; then
  install -o root -g radon -m 0640 "$source_dir/slm.json.example" /etc/radon/slm.json
fi
if [[ ! -e /etc/radon/slm-secrets.env ]]; then
  # The API key never goes through argv, stdout, the checkout, or shell history.
  python3 - <<'PY'
import os
import secrets

fd = os.open("/etc/radon/slm-secrets.env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    handle.write("VLLM_API_KEY=" + secrets.token_urlsafe(48) + "\n")
PY
fi
systemctl daemon-reload
echo 'Installed GPU runtime. No enable/start/restart performed.'
echo 'Set slm.json tailscale_ip, then run radon-slm-runtime pull and check.'
