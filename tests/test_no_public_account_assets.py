from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_public_site_does_not_ship_account_bearing_dashboard_plates():
    plates = ROOT / "site" / "public" / "plates"
    assert not list(plates.glob("dashboard-*"))


def test_public_site_references_only_the_synthetic_portfolio_hero_plate():
    hero = (ROOT / "site" / "components" / "sections" / "EditorialHeroSection.tsx").read_text()
    assert 'shot="dashboard"' not in hero
    assert 'shot="portfolio"' in hero
    assert "Synthetic demonstration" in hero
