"""Offline replay harness: corpus loading, golden set, v1 baseline and metrics.

Fixtures are synthetic; the private VPS corpus is exercised by the same code
through `python -m research.harness --root <private-root>`.
"""
import json
import sqlite3

import pytest

from research import harness


def _work_row(key, name, status, result, folder_date="2026-09-10", publisher="goldman sachs"):
    path = f"/joe mccann/current/2026/september/sep 10/{publisher}/{name}"
    meta = {".tag": "file", "id": "id:" + key, "rev": "r1", "content_hash": "a" * 64,
            "name": name, "path_lower": path, "path_display": path,
            "client_modified": "2026-09-10T08:00:00Z", "server_modified": "2026-09-10T08:00:00Z"}
    return (key, "id:" + key, "r1", path, "2026/september/sep 10", folder_date,
            json.dumps(meta), status, 1, 0, None, json.dumps(result) if result else None)


@pytest.fixture
def corpus_root(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    db = sqlite3.connect(root / "state.sqlite")
    db.executescript("""
    CREATE TABLE work(key TEXT PRIMARY KEY, file_id TEXT, rev TEXT, path TEXT, scope TEXT, folder_date TEXT,
      metadata TEXT, status TEXT, attempts INTEGER, available_at REAL, error TEXT, result TEXT);
    CREATE TABLE outbox(id TEXT PRIMARY KEY, work_key TEXT, payload TEXT, status TEXT);
    """)
    rows = [
        _work_row("k" * 64, "flows.pdf", "published", {"status": "reviewed", "items": 1}),
        _work_row("m" * 64, "morning meeting.pdf", "complete", {"status": "reviewed", "items": 0}),
        _work_row("q" * 64, "quota.pdf", "complete", {"status": "held", "error": "Model ladder exhausted after trying every keyed provider"}),
        _work_row("s" * 64, "old.pdf", "superseded", {"status": "reviewed", "items": 0}),
    ]
    db.executemany("INSERT INTO work VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO outbox VALUES (?,?,?,?)", ("research-" + "1" * 32, "k" * 64,
               json.dumps({"id": "research-" + "1" * 32, "title": "JPY demand rises", "tags": ["JPY"],
                           "source": {"pages": [2], "documentDate": "2026-09-10"}}), "published"))
    db.commit(); db.close()
    for key, pages, review in (
        ("k" * 64, ["# Flows\n8 September 2026 | 9:00AM BST", "Chart 1\nSource: GS"],
         {"audit": [{"selection": {"candidates": [{}, {}]}}, {"inspection": {}}, {"verification": {}}], "posts": [{"id": "research-" + "1" * 32}]}),
        ("m" * 64, ["Morning meeting"], {"audit": [{"selection": {"candidates": []}}], "posts": []}),
        ("q" * 64, ["Quota"], None),
    ):
        d = root / "evidence" / key
        d.mkdir(parents=True)
        for i, text in enumerate(pages, 1):
            (d / f"page-{i:04d}.md").write_text(text)
        (d / "evidence.json").write_text(json.dumps({"page_count": len(pages), "pages": [
            {"page_number": i, "markdown_file": f"page-{i:04d}.md", "needs_ocr": False} for i in range(1, len(pages) + 1)]}))
        if review:
            (d / "review.json").write_text(json.dumps(review))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({"labels": {"m" * 16: "out", "q" * 16: "in", "k" * 16: "in"}}))
    return root, labels


def test_corpus_loads_live_work_with_cached_evidence(corpus_root):
    root, _ = corpus_root
    docs = {d.key: d for d in harness.Corpus(root).documents()}
    assert set(docs) == {"k" * 64, "m" * 64, "q" * 64}, "superseded rows are not corpus members"
    doc = docs["k" * 64]
    assert doc.publisher_folder == "goldman sachs"
    assert doc.page_count == 2
    assert doc.page_text(1).startswith("# Flows")
    assert doc.metadata["client_modified"] == "2026-09-10T08:00:00Z"


def test_golden_joins_published_posts_and_operator_labels(corpus_root):
    root, labels = corpus_root
    golden = harness.Golden.load(root, labels)
    assert golden.positives == {"k" * 64: ["JPY demand rises"]}
    assert golden.label("m" * 64) == "out"
    assert golden.label("q" * 64) == "in"
    assert golden.label("x" * 64) is None
    assert golden.in_scope == {"k" * 64, "q" * 64}


def test_v1_baseline_reads_calls_candidates_and_holds(corpus_root):
    root, labels = corpus_root
    report = harness.baseline_v1(harness.Corpus(root), harness.Golden.load(root, labels))
    assert report["docs"] == 3
    assert report["model_calls"] == 4
    assert report["candidates"] == 2
    assert report["docs_with_posts"] == 1
    assert report["quota_holds"] == 1
    assert report["positive_recall"] == 1.0
    assert report["in_scope_reached_model"] == 0.5, "quota-held in-scope doc never reached the model"


def test_evaluate_scores_a_stage_function_against_golden(corpus_root):
    root, labels = corpus_root
    corpus, golden = harness.Corpus(root), harness.Golden.load(root, labels)

    def run(doc):
        if "morning" in doc.name:
            return harness.Outcome(dropped="DOC_TYPE_MORNING_MEETING")
        return harness.Outcome(posts=["JPY demand rises"] if doc.key.startswith("k") else [], calls=2)

    report = harness.evaluate(run, corpus, golden)
    assert report["docs"] == 3
    assert report["dropped"] == {"DOC_TYPE_MORNING_MEETING": 1}
    assert report["positive_recall"] == 1.0
    assert report["in_scope_dropped"] == 0
    assert report["out_scope_dropped"] == 1
    assert report["calls_per_doc"] == pytest.approx(4 / 3)
    assert report["docs_with_posts"] == 1


def test_evaluate_flags_in_scope_drops_and_lost_positives(corpus_root):
    root, labels = corpus_root
    corpus, golden = harness.Corpus(root), harness.Golden.load(root, labels)
    report = harness.evaluate(lambda doc: harness.Outcome(dropped="EVERYTHING"), corpus, golden)
    assert report["positive_recall"] == 0.0
    assert report["in_scope_dropped"] == 2
    assert sorted(report["lost_positives"]) == ["k" * 64]


def test_cli_prints_baseline_json(corpus_root, capsys):
    root, labels = corpus_root
    harness.main(["--root", str(root), "--labels", str(labels), "--baseline"])
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "v1-baseline" and out["docs"] == 3


def test_mirror_outcomes_backfills_only_v2_audits(corpus_root):
    root, _ = corpus_root
    review = root / "evidence" / ("m" * 64) / "review.json"
    review.write_text(json.dumps({"pipeline": "v2", "outcome": "reviewed", "identity": {"publisher": "J.P. Morgan"}, "audit": [], "posts": []}))
    seen = []
    assert harness.mirror_outcomes(harness.Corpus(root), record=lambda work, r: seen.append((work["key"], work["metadata"]["name"], r["pipeline"]))) == 1
    assert seen == [("m" * 64, "morning meeting.pdf", "v2")]
