"""Exercise only the capture's env sections against synthetic local files."""
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "phase0_capture.sh"


def test_live_root_env_is_in_all_diagnostic_sections_and_secrets_are_redacted(tmp_path):
    text = SCRIPT.read_text()
    root = tmp_path / "root.env"
    legacy = tmp_path / "legacy.env"
    web = tmp_path / "web.env"
    root.write_text("RADON_MODE=hetzner\nTURSO_DB_URL=libsql://synthetic.example.test\nTURSO_AUTH_TOKEN=synthetic-secret\nCLIENT_ID=91\nCLIENT_SECRET=synthetic-client-secret\nCLIENT_ID_TOKEN=synthetic-client-id-token\n")
    legacy.write_text("RADON_MODE=legacy\n")
    text = text.replace("/etc/radon/env", str(root)).replace("/home/radon/radon-cloud/web/.env", str(web)).replace("/home/radon/radon-cloud/.env", str(legacy))
    functions = text[:text.index('sec "HOST BASICS"')]
    env = text[text.index('sec "ENV FILES'):text.index('sec "DOCKER')]
    client = text[text.index('sec "CLIENT-ID'):text.index('sec "CAPTURE COMPLETE')]
    result = subprocess.run(["bash"], input=functions + env + client, text=True, capture_output=True, check=True)
    surface, db = result.stdout.split("TURSO / DB references", 1)
    db, client = db.split("CLIENT-ID usage hints", 1)
    assert "RADON_MODE=hetzner" in surface
    assert "RADON_MODE=legacy" in surface
    assert "(absent)" in surface
    assert "TURSO_DB_URL=libsql://synthetic.example.test" in db
    assert "CLIENT_ID=91" in client
    assert "CLIENT_SECRET=<redacted:" in surface
    assert "CLIENT_ID_TOKEN=<redacted:" in client
    assert "synthetic-client-id-token" not in result.stdout
    assert "TURSO_AUTH_TOKEN=<redacted:" in db
    assert "synthetic-secret" not in result.stdout
    assert "synthetic-client-secret" not in result.stdout
