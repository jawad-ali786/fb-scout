import json

from fbscout.storage import RunWriter, list_runs, record_id, slugify


def test_slugify():
    assert slugify("Solar Panel!") == "solar-panel"
    assert slugify("کراچی بیکری") == "کراچی-بیکری"
    assert slugify("???") == "keyword"


def test_record_id_stable():
    assert record_id("a") == record_id("a") != record_id("b")
    assert record_id("a").startswith("r_") and len(record_id("a")) == 14


def test_run_writer_roundtrip(tmp_path):
    run = RunWriter(tmp_path, "Solar Panel", {"scope": "search:posts", "match_mode": "phrase"})
    assert (run.dir / "screenshots").is_dir()
    name, path = run.screenshot_target(3, "group_post", "r_abcdef123456")
    assert name == "003_group_post_abcdef12.png" and path.parent == run.screenshots_dir

    run.add({"id": "r_1", "kind": "post", "screenshot_name": name, "text": "long text"})
    run.warn("w1")
    run.warn("w1")
    run.finish("completed")

    data = json.loads((run.dir / "results.json").read_text(encoding="utf-8"))
    assert data["run"]["status"] == "completed"
    assert data["run"]["stats"]["saved"] == 1
    assert data["run"]["warnings"] == ["w1"]
    assert data["results"][0]["id"] == "r_1"

    summary = run.summary()
    assert summary["ok"] and "text" not in summary["records"][0]

    runs = list_runs(tmp_path)
    assert len(runs) == 1 and runs[0]["keyword"] == "Solar Panel"
    assert list_runs(tmp_path, keyword="other") == []


def test_two_runs_same_second_get_different_folders(tmp_path):
    a = RunWriter(tmp_path, "k", {})
    b = RunWriter(tmp_path, "k", {})
    assert a.dir != b.dir
