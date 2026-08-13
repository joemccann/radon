from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_client_fallback_contains_no_hardcoded_account_snapshot():
    source = (ROOT / "web" / "lib" / "chat.ts").read_text()
    forbidden = ["$981,353", "19 positions total", "long 300x Mar 20 calls"]
    assert all(value not in source for value in forbidden)
