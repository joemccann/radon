"""Chart plans copied from a finding. The plan has to match the series."""
from research import charts

GS = "\n\n".join([
    "Rates are getting into the AI credit story, GS Johnstone notes. The 10-year is back above 5.10% and the 2-year sits at 4.90% after the hot September PMI, oil and the hawkish Fed tone. The desk says the speed is what matters: the 10-year is up ~25 bp in two weeks and ~35 bp over the past month, which is close to where equities usually start to care, especially with the S&P still trading around ~19x.",
    "The clearest stress test is SoftBank. Its ~$11.1bn HY raise to fund the final $10bn OpenAI tranche and refinance bridge debt cleared with strong demand, but at record dollar yields of 8.6-9.75%. That is the tell for AI-linked credit. Earlier in the week, CoreWeave's Virginia data-centre financing was still marketing in the low- to mid-9% area. The desk's read: markets will still fund AI infrastructure and neocloud project finance, even with pre-revenue construction risk. But they are not doing it cheap. Credit investors only want near-10% coupons.",
    "The desk prices a 71% chance of an October hike and about 36 bp of tightening by year-end.",
])

DLT = "\n".join([
    "Global DLT fixed-income issuance reached €4.8bn in 2025, up 48% from 2024, finance at €3.45bn",
    "Global DLT fixed-income issuance reached €4.8bn in 2025, up 48% from 2024. Finance led global digital bond issuance by issuer type in 2025 at €3.45bn. Government issued €1.29bn. Construction was €47mn, supranational €6mn, technology €5mn and energy €2mn.",
])


def test_gs_finding_plans_a_yield_range_and_a_basis_point_bar():
    plans = charts.plan(GS)
    assert [plan["kind"] for plan in plans] == ["range", "bar"]
    yields, move = plans
    assert yields["title"] == "Dollar yields" and yields["axis"] == [0, 12]
    assert yields["reference"] == {"value": 10, "label": "near 10%"}
    assert [(mark["label"], mark["estimated"], mark["low"], mark["high"]) for mark in yields["marks"]] == [
        ("SoftBank HY", False, 8.6, 9.75),
        ("CoreWeave VA", True, 9, 9.5),
        ("10-year", False, 5.1, 5.1),
        ("2-year", False, 4.9, 4.9),
    ]
    assert yields["marks"][0]["detail"] == "$11.1bn"
    assert [(bar["label"], bar["value"]) for bar in move["bars"]] == [("2 weeks", 25), ("1 month", 35)]
    assert "71" not in str(plans) and "36" not in str(plans)


def test_thin_findings_do_not_invent_a_chart():
    assert charts.plan("The 10-year closed at 4.2%.") == []
    assert charts.plan("The 10-year rose 12bp in two weeks.") == []
    assert charts.plan("Markets price a 71% chance of an October hike.") == []
    assert len(charts.plan("SoftBank cleared at 8.6-9.75%.")) == 1


def test_issuance_bar_drops_the_total_and_the_growth_rate():
    plans = charts.plan(DLT)
    assert len(plans) == 1 and plans[0]["kind"] == "bar" and plans[0]["unit"] == "€bn"
    assert [(bar["label"], bar["value"]) for bar in plans[0]["bars"]] == [
        ("Finance", 3.45), ("Government", 1.29), ("Construction", 0.047),
        ("Supranational", 0.006), ("Technology", 0.005), ("Energy", 0.002),
    ]


def test_two_dollar_flows_are_a_bar():
    plans = charts.plan("Foreign investors bought $45bn of US equities. Official investors added $12bn.")
    assert plans[0]["kind"] == "bar"
    assert [(bar["label"], bar["value"]) for bar in plans[0]["bars"]] == [("Foreign investors", 45), ("Official investors", 12)]
