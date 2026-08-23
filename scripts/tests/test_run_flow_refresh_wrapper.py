"""Source contract for scripts/run_flow_refresh.sh."""
from pathlib import Path


def test_wrapper_posts_three_flow_tabs_and_cheap_discover() -> None:
    source = (Path(__file__).resolve().parents[1] / "run_flow_refresh.sh").read_text()
    assert 'refresh_scan "scanner" "/scan?force=true"' in source
    assert 'refresh_scan "flow-analysis" "/flow-analysis?force=true"' in source
    assert 'refresh_scan "discover" "/discover?force=true"' in source
    assert "--min-alerts 3" in source
    assert "--dp-pages 2" in source
    assert "CURL_EXIT" in source
    assert "%{http_code}" in source


def test_wrapper_forces_every_scheduled_post_past_the_cooldown() -> None:
    source = (Path(__file__).resolve().parents[1] / "run_flow_refresh.sh").read_text()
    scheduled_posts = [line for line in source.splitlines() if line.startswith("refresh_scan ")]
    assert len(scheduled_posts) == 3
    assert all("?force=true" in line for line in scheduled_posts)
