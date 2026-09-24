"""Always-include match predicates for BofA Flow Show and DB positioning data."""
from types import SimpleNamespace

import pytest

from research import force_include, identify


def ident(publisher, series='', folder=''):
    return SimpleNamespace(publisher=publisher, series=series, publisher_folder=folder)


@pytest.mark.parametrize("identity,filename,expected", [
    (ident("BofA Global Research", "the flow show"), None, True),
    (ident("BofA Global Research", "flow show"), None, True),
    (ident("BofA Global Research", ""), "The_Flow-Show_19_Sep.pdf", True),
    (ident("unknown", "the flow show", "bank of america"), None, True),
    (ident("BofA Global Research", "equity strategy"), None, False),
    (ident("Goldman Sachs", "the flow show"), "the flow show friday.pdf", False),
    (ident("Deutsche Bank Research", "db positioning data"), None, True),
    (ident("Deutsche Bank Research", "db positioning"), None, True),
    (ident("Deutsche Bank Research", ""), "DB_Positioning_Data_19_Sep.pdf", True),
    (ident("unknown", "positioning data", "deutsche bank"), None, True),
    (ident("Deutsche Bank Research", "fx positioning weekly"), None, False),
    (ident("Deutsche Bank Research", "equity strategy"), None, False),
    (ident("UBS", "positioning data"), "positioning data.pdf", False),
    (ident("Goldman Sachs", "db positioning data"), None, False),
])
def test_matches_requires_publisher_and_tight_series(identity, filename, expected):
    assert force_include.matches(identity, filename=filename) is expected


def test_real_identity_from_bofa_flow_show_filename():
    path = "/joe mccann/current/2026/september/sep 19/bank of america/the flow show friday, 19 september 2026.pdf"
    found = identify.identify(
        {1: "## The Flow Show ## 19 September 2026"},
        {"name": "the flow show friday, 19 september 2026.pdf", "path_lower": path,
         "client_modified": "2026-09-19T08:00:00Z"},
        "2026-09-19")
    assert found.publisher == "BofA Global Research"
    assert "flow show" in found.series
    assert force_include.matches(found, filename=found.series) is True


def test_real_identity_from_db_positioning_filename():
    path = "/joe mccann/current/2026/september/sep 19/deutsche bank/db positioning data - 19 september 2026.pdf"
    found = identify.identify(
        {1: "Deutsche Bank Research 19 September 2026"},
        {"name": "db positioning data - 19 september 2026.pdf", "path_lower": path,
         "client_modified": "2026-09-19T08:00:00Z"},
        "2026-09-19")
    assert found.publisher == "Deutsche Bank Research"
    assert "positioning data" in found.series
    assert force_include.matches(found) is True
