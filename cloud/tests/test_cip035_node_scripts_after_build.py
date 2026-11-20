"""CIP-035: node image must not rebuild Next on a scripts-only push.

`COPY scripts/` currently sits above `bun run build`, so a python-only
push still unpacks dependency layers and reruns Turbopack. Mixed run
44594349045 job 135808679573 spent 171s on the node image, last required
gate before deploy, with `COPY scripts/` then `COPY web/` then
`bun run build`. The Next build reads only
`scripts/config/market_holidays.json` and
`scripts/config/market_early_closes.json`
(`web/lib/marketCalendar.ts`). Copy those two files before the build;
copy the rest of `scripts/` after it. Exact SHA tags, health, and the
40-second stability window stay.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "app" / "Dockerfile.node"
HOLIDAYS = "COPY scripts/config/market_holidays.json scripts/config/market_early_closes.json ./scripts/config/"
SCRIPTS = "COPY scripts/ ./scripts"
WEB = "COPY web/ ./web"
BUILD = "RUN bun run build"


def _node() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_next_build_sees_only_the_holiday_tables_from_scripts() -> None:
    node = _node()
    assert HOLIDAYS in node
    assert node.index(HOLIDAYS) < node.index(WEB)
    assert node.index(WEB) < node.index(BUILD)
    assert node.index(BUILD) < node.index(SCRIPTS)


def test_full_scripts_tree_still_lands_in_the_image() -> None:
    node = _node()
    assert SCRIPTS in node
    assert "USER radon" in node
    assert node.index(SCRIPTS) < node.index("USER radon")
    # Dependency install stays ahead of changing source.
    assert node.index("RUN bun install --frozen-lockfile") < node.index(HOLIDAYS)
    assert node.index("playwright install") < node.index(HOLIDAYS)
