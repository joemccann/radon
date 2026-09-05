"""R-665: the app-plane image installs playwright browsers with no record of
what was fetched. The build must compute and record a digest of the installed
browser tree so any two images (or an audit) can compare what actually landed.
Filesystem pin — no docker build required."""

from pathlib import Path

DOCKERFILE = (
    Path(__file__).resolve().parents[2] / "docker" / "app" / "Dockerfile.python"
)


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
