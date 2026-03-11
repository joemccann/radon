from performance_explainer_report import (
    build_html,
    chart_family_contract,
    chart_role_color,
    load_chart_system,
    load_payload,
)


def test_chart_family_contract_comes_from_shared_chart_system():
    chart_system = load_chart_system()

    contract = chart_family_contract(chart_system, "analytical-time-series")

    assert contract["id"] == "analytical-time-series"
    assert contract["label"] == "Analytical Time Series"
    assert contract["renderer"] == "svg"
    assert contract["requires_axes"] is True
    assert contract["renderer_description"] == chart_system["sanctionedRenderers"]["svg"]
    assert chart_role_color(chart_system, "comparison") == chart_system["seriesRoles"]["comparison"]["fallback"]


def test_build_html_mentions_shared_chart_contract():
    payload = load_payload()
    chart_system = load_chart_system()

    report_html = build_html(payload, chart_system)

    assert "web/lib/chart-system-spec.json" in report_html
    assert "PRIMARY / COMPARISON" in report_html
    assert "ANALYTICAL TIME SERIES" in report_html
    assert "This panel uses the shared chart-system contract" in report_html
