"""CIP-028: install torch before copying vendor CLIs into the gateway image.

Scripts-only mixed rebuild 42421162278 job 128549861709 spent 234s on
Build and publish model gateway image: runtime-deps layers were CACHED,
then pip still collected torch==2.12.1+cpu at +58s, then load:true sent a
71.3s tarball. CIP-025 moved COPY scripts after the lock; COPY --from=
vendor-clis and vendor-clis-darwin still precede it, so a CIP-027 sibling
runner that misses those CLI layers also rebuilds torch.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "app" / "Dockerfile.python"


def _gateway_stage() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text.split("FROM runtime-deps AS model-gateway", 1)[1].split(
        "FROM runtime AS python", 1
    )[0]


def test_gateway_torch_install_precedes_vendor_cli_copies() -> None:
    gateway = _gateway_stage()
    torch_at = gateway.index("-r requirements-model-gateway.txt")
    assert torch_at < gateway.index("COPY --from=vendor-clis ")
    assert torch_at < gateway.index("COPY --from=vendor-clis-darwin")
    assert torch_at < gateway.index("install-antigravity.py")
    assert torch_at < gateway.index("COPY scripts ./scripts")


def test_gateway_still_copies_clis_and_scripts_after_the_lock() -> None:
    gateway = _gateway_stage()
    torch_at = gateway.index("-r requirements-model-gateway.txt")
    assert "COPY --from=vendor-clis /opt/radon-clis" in gateway
    assert "COPY --from=vendor-clis-darwin /opt/radon-darwin" in gateway
    assert "COPY scripts ./scripts" in gateway
    assert gateway.index("USER radon") > torch_at
    assert "chronos-forecasting" in gateway or "import chronos, torch" in gateway
