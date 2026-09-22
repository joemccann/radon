"""CRI HTML report must escape vendor-feed strings and never auto-open.

MenthorQ table fields are vendor-controlled text interpolated into an HTML
file the operator opens locally — markup in a feed value must render inert.
Opening the report in a browser must be opt-in (--open), never the default.
"""
from __future__ import annotations

import webbrowser

import cri_scan


MARKER = '<script>alert("x")</script>'


def _result_with_vendor_markup():
    conditions = {
        "spx_below_100d_ma": False,
        "realized_vol_gt_25": False,
        "cor1m_gt_60": False,
    }
    entry = {
        "underlying": MARKER,
        "position_today": MARKER,
        "position_yesterday": "1.00",
        "position_1m_ago": "1.00",
        "percentile_3m": 50,
        "z_score_3m": MARKER,
    }
    return {
        "date": "2026-09-17",
        "vix": 15.0,
        "vix_5d_roc": 0.0,
        "vvix": 90.0,
        "vvix_vix_ratio": 6.0,
        "cor1m": 30.0,
        "cor1m_5d_change": 0.0,
        "spy": 500.0,
        "spx_100d_ma": 490.0,
        "spx_distance_pct": 2.0,
        "realized_vol": 12.0,
        "cri": {
            "score": 10.0,
            "level": "LOW",
            "components": {"vix": 2.0, "vvix": 2.0, "correlation": 3.0, "momentum": 3.0},
        },
        "cta": {
            "realized_vol": 12.0,
            "exposure_pct": 83.0,
            "forced_reduction_pct": 0.0,
            "est_selling_bn": 0.0,
        },
        "crash_trigger": {"triggered": False, "conditions": conditions},
        "history": [
            {
                "date": MARKER,
                "vix": 15.0,
                "vvix": 90.0,
                "spy": 500.0,
                "spx_vs_ma_pct": 2.0,
                "vix_5d_roc": 0.0,
            }
        ],
        "menthorq_cta": {
            "date": MARKER,
            "spx": {
                "position_today": 1.0,
                "position_yesterday": MARKER,
                "position_1m_ago": MARKER,
                "percentile_3m": 50,
                "percentile_1y": 50,
                "z_score_3m": 0.0,
            },
            "tables": {"main": [entry], "commodity": [dict(entry)]},
        },
    }


def test_vendor_strings_are_escaped_in_report():
    html = cri_scan.generate_html_report(_result_with_vendor_markup(), True, 1.0)
    assert MARKER not in html, "vendor markup interpolated unescaped"
    assert "&lt;script&gt;" in html, "vendor string should survive, escaped"


def test_open_flag_is_opt_in():
    parser = cri_scan.build_parser()
    assert parser.parse_args([]).open is False
    assert parser.parse_args(["--open"]).open is True


def test_report_does_not_auto_open_without_open_flag(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    out_path = tmp_path / "cri.html"

    cri_scan.emit_html_report(
        "<html></html>", out_path, open_browser=False
    )
    assert opened == [], "report must not auto-open without --open"
    assert out_path.read_text() == "<html></html>"

    cri_scan.emit_html_report("<html></html>", out_path, open_browser=True)
    assert opened == [f"file://{out_path.resolve()}"]
