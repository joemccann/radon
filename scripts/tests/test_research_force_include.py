"""Always-include desks: BofA Flow Show, DB positioning, Citadel Rubner GMI."""
from types import SimpleNamespace

import pytest

from research import force_include, identify
from tests.test_research_intake import work


def ident(publisher, series='', folder=''):
    return SimpleNamespace(publisher=publisher, series=series, publisher_folder=folder)


RUBNER_PAGE = (
    "CS Rubner-October: The Q4 Reload 1 Oct 2026 Scott Rubner · Citadel Securities "
    "· Global Market Intelligence. We are turning more constructive on US equities into Q4."
)


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


@pytest.mark.parametrize("identity,filename,page_text,expected", [
    (ident("Citadel", "cs rubner october the q4 reload"), None, None, True),
    (ident("Citadel Securities", "the q4 reload"), "CS_Rubner_October.pdf", None, True),
    (ident("Citadel", "the q4 reload"), "Citadel - The Q4 Reload October 1 Oct 2026.pdf", RUBNER_PAGE, True),
    (ident("Citadel", "gmi weekly"), None, None, True),
    (ident("Citadel", "global markets intelligence"), None, None, True),
    (ident("unknown", "the q4 reload", ""), None, RUBNER_PAGE, True),
    (ident("Citadel", "prime financing weekly"), None, "Jane Smith · Citadel Securities Prime", False),
    (ident("Citadel", "equities desk note"), None, "Alex Other · Citadel Securities", False),
    (ident("Goldman Sachs", "cs rubner october"), "cs rubner.pdf", RUBNER_PAGE, False),
])
def test_rubner_detection_variants(identity, filename, page_text, expected):
    assert force_include.matches(identity, filename=filename, page_text=page_text) is expected
    desk = force_include.match(identity, filename=filename, page_text=page_text)
    if expected:
        assert desk is not None and desk.name == "citadel_rubner"
        assert desk.always_publish is True and desk.priority is True
    else:
        assert desk is None or desk.name != "citadel_rubner"


def test_bofa_and_db_desks_are_not_always_publish():
    bofa = force_include.match(ident("BofA Global Research", "the flow show"))
    db = force_include.match(ident("Deutsche Bank Research", "db positioning data"))
    assert bofa is not None and bofa.always_publish is False
    assert db is not None and db.always_publish is False


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


def test_real_identity_from_oct1_rubner_q4_reload():
    root = work()["metadata"]["path_lower"].rsplit("/", 4)[0]
    path = f"{root}/october/oct 01/citadel/citadel - the q4 reload october 1 oct 2026.pdf"
    found = identify.identify(
        {1: RUBNER_PAGE},
        {"name": "Citadel - The Q4 Reload October 1 Oct 2026.pdf", "path_lower": path,
         "client_modified": "2026-10-01T08:00:00Z"},
        "2026-10-01")
    assert found.publisher == "Citadel"
    assert force_include.matches(found, filename=found.series, page_text=RUBNER_PAGE) is True
    assert force_include.always_publish(found, filename="Citadel - The Q4 Reload October 1 Oct 2026.pdf",
                                       page_text=RUBNER_PAGE) is True


def test_fallback_copy_has_attribution_and_no_ungrounded_numbers():
    identity = SimpleNamespace(publisher="Citadel", series="the q4 reload", date="2026-10-01",
                               date_page=1, date_source="text")
    title, content, pages = force_include.fallback_copy(identity, {1: RUBNER_PAGE})
    assert "Scott Rubner" in content and "Citadel Securities" in content
    assert "2.35" not in title + content and "-0.80" not in title + content
    assert pages == [1]
    from research import ground
    assert ground.ground([title, content], {1: RUBNER_PAGE}, pages,
                         known={"date": "2026-10-01", "date_page": 1})["passed"]
