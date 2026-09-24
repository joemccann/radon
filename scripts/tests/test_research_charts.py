"""Chart plans copied from a finding. The GS note is the proof fixture."""
from research import charts

GS = "\n\n".join([
    "Rates are getting into the AI credit story, GS Johnstone notes. The 10-year is back above 5.10% and the 2-year sits at 4.90% after the hot September PMI, oil and the hawkish Fed tone. The desk says the speed is what matters: the 10-year is up ~25 bp in two weeks and ~35 bp over the past month, which is close to where equities usually start to care, especially with the S&P still trading around ~19x.",
    "The clearest stress test is SoftBank. Its ~$11.1bn HY raise to fund the final $10bn OpenAI tranche and refinance bridge debt cleared with strong demand, but at record dollar yields of 8.6-9.75%. That is the tell for AI-linked credit. Earlier in the week, CoreWeave's Virginia data-centre financing was still marketing in the low- to mid-9% area. The desk's read: markets will still fund AI infrastructure and neocloud project finance, even with pre-revenue construction risk. But they are not doing it cheap. Credit investors only want near-10% coupons.",
    "The desk prices a 71% chance of an October hike and about 36 bp of tightening by year-end.",
])


def test_gs_finding_plans_yields_and_a_separate_rates_chart():
    plans = charts.plan(GS)
    assert [plan["kind"] for plan in plans] == ["levels", "move"]
    levels, move = plans
    assert levels["title"] == "Dollar yields" and levels["axis"] == [0, 12]
    assert levels["dek"] == "S&P near 19x."
    assert levels["reference"] == {"value": 10, "label": "near 10%"}
    assert [(mark["label"], mark["kind"], mark["low"], mark["high"]) for mark in levels["marks"]] == [
        ("SoftBank HY", "printed-range", 8.6, 9.75),
        ("CoreWeave VA", "desk-band", 9, 9.5),
        ("US 10-year", "point", 5.1, 5.1),
        ("US 2-year", "point", 4.9, 4.9),
    ]
    assert levels["marks"][0]["detail"] == "$11.1bn"
    assert move["axisMax"] == 40
    assert [(bar["label"], bar["bp"]) for bar in move["bars"]] == [("1 month", 35), ("2 weeks", 25)]
    assert move["probability"] == {"label": "October hike", "pct": 71}
    assert move["readout"] == {"label": "Year-end tightening", "bp": 36}


def test_thin_findings_do_not_invent_a_chart():
    assert charts.plan("The 10-year closed at 4.2%.") == []
    assert charts.plan("The 10-year rose 12bp in two weeks.") == []
    assert charts.plan("Foreign investors bought $45bn of US equities. Official investors added $12bn.") == []
    assert len(charts.plan("SoftBank cleared at 8.6-9.75%.")) == 1
