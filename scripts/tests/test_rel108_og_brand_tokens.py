"""REL-108 / R-316: exported plates retain the accessible brand-kit palette."""
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOKENS = json.loads((ROOT / 'brand/radon-design-tokens.json').read_text())['color']['dark']
SOURCE = (ROOT / 'web/lib/og-theme.ts').read_text()
BRAND = dict(re.findall(r'^  (\w+): "(#[0-9a-fA-F]{6})",', SOURCE, re.M))


@pytest.mark.parametrize(('role', 'category', 'name'), [
    ('bg', 'bg', 'canvas'), ('panel', 'bg', 'panel'),
    ('panelRaised', 'bg', 'panelRaised'), ('border', 'line', 'grid'),
    ('text', 'text', 'primary'), ('muted', 'text', 'secondary'),
    ('faint', 'text', 'muted'),
])
def test_exported_plate_tokens_match_accessible_brand_kit(role, category, name):
    # Clear's web UI has its own newer palette; the export kit remains the
    # documented source for these brand-colored plates, not the old literal.
    assert BRAND[role].lower() == TOKENS[category][name].lower()


def luminance(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


@pytest.mark.parametrize('surface', ['bg', 'panel'])
def test_supporting_plate_text_clears_normal_text_contrast(surface):
    ratio = (luminance(BRAND['faint']) + 0.05) / (luminance(BRAND[surface]) + 0.05)
    assert ratio >= 4.5
