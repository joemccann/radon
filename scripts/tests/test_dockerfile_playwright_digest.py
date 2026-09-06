"""R-665: the app-plane image installs playwright browsers with no record of
what was fetched. The build must compute and record a digest of the installed
browser tree so any two images (or an audit) can compare what actually landed.
Filesystem pin — no docker build required."""

import subprocess
from pathlib import Path

DOCKERFILE = (
    Path(__file__).resolve().parents[2] / "docker" / "app" / "Dockerfile.python"
)

# sha256 of the empty stream — what the pipeline must NEVER record.
EMPTY_STREAM_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)


def _digest_pipeline() -> str:
    """Extract the digest-producing shell fragment from the install RUN layer:
    every &&-joined command after the playwright install, up to and including
    the one that writes .browsers.sha256."""
    source = DOCKERFILE.read_text()
    install_layer = next(
        chunk
        for chunk in source.split("RUN ")
        if chunk.startswith("python -m playwright install")
    )
    flat = install_layer.replace("\\\n", " ")
    segments = [s.strip() for s in flat.split("&&")]
    start = 1  # segment 0 is the install itself
    end = next(
        i for i, s in enumerate(segments) if ".browsers.sha256" in s and ">" in s
    )
    return " && ".join(segments[start : end + 1])


def _run_pipeline(tree: Path) -> subprocess.CompletedProcess:
    script = _digest_pipeline().replace("/ms-playwright", str(tree))
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True
    )


def test_populated_tree_yields_non_empty_digest(tmp_path):
    tree = tmp_path / "browsers"
    (tree / "chromium").mkdir(parents=True)
    (tree / "chromium" / "shell").write_bytes(b"fake browser bytes")
    result = _run_pipeline(tree)
    assert result.returncode == 0, result.stderr
    digest = (tree / ".browsers.sha256").read_text().strip()
    assert len(digest) == 64 and digest != EMPTY_STREAM_SHA256


def test_empty_tree_fails_instead_of_recording_empty_digest(tmp_path):
    tree = tmp_path / "browsers"
    tree.mkdir()
    result = _run_pipeline(tree)
    assert result.returncode != 0, (
        "an empty browser tree must fail the build, not record the "
        f"empty-stream digest: {result.stdout!r}"
    )
    digest_file = tree / ".browsers.sha256"
    if digest_file.exists():
        assert digest_file.read_text().strip() != EMPTY_STREAM_SHA256


def test_digest_has_an_in_image_consumer():
    """Nothing else in the repo reads .browsers.sha256, so the build itself
    must consume it: assert the recorded digest is 64 hex chars and not the
    empty-stream sha256, failing the image build otherwise."""
    source = DOCKERFILE.read_text()
    assert EMPTY_STREAM_SHA256 in source, (
        "the build must reject the empty-stream digest explicitly"
    )
    assert "grep -qE" in source and "[0-9a-f]{64}" in source


def test_build_records_browser_tree_digest():
    source = DOCKERFILE.read_text()
    assert "sha256sum" in source, "browser install must be digested at build"
    assert "/ms-playwright/.browsers.sha256" in source, (
        "the digest must be recorded inside the image next to the browsers"
    )


def test_digest_step_is_chained_to_the_install():
    """The digest is computed in the SAME RUN layer as the install, so a
    cached or edited install step cannot drift from its recorded digest."""
    source = DOCKERFILE.read_text()
    install_layer = next(
        chunk
        for chunk in source.split("RUN ")
        if chunk.startswith("python -m playwright install")
    )
    assert ".browsers.sha256" in install_layer
