import asyncio
import json

import pytest

from fbscout.batch import MAX_SEARCHES, Study, run_batch


@pytest.fixture(autouse=True)
def no_delays(monkeypatch):
    monkeypatch.setenv("FBSCOUT_PACE", "0")


def test_study_plan_and_validation(tmp_path):
    f = tmp_path / "solar.json"
    f.write_text(json.dumps({
        "keywords": ["solar panel", " Solar Panel ", "inverter"],
        "groups": ["https://www.facebook.com/groups/123/posts/9/?__cft__[0]=x", "https://facebook.com/groups/123"],
        "max_results": 5,
    }), encoding="utf-8")
    study = Study.load(f)
    assert study.name == "solar"                                   # from the file name
    assert study.keywords == ["solar panel", "inverter"]          # case-insensitive duplicates dropped
    assert study.groups == ["https://www.facebook.com/groups/123/"]   # both URLs are the same group
    plan = study.plan("b1")
    assert [(o.keyword, o.group_url) for o in plan][:2] == [("solar panel", None),
                                                            ("solar panel", "https://www.facebook.com/groups/123/")]
    assert all(o.max_results == 5 and o.batch_id == "b1" for o in plan)
    assert study.describe()["searches"] == len(plan)


@pytest.mark.parametrize("data, message", [
    ({"keywords": []}, "at least one keyword"),
    ({"groups": ["https://www.facebook.com/groups/1/"]}, "keywords"),
    ({"keywords": ["x"], "groups": ["https://www.facebook.com/SolarCo"]}, "Not a Facebook group URL"),
    ({"keywords": ["x"], "include_global": False}, "Nothing to search"),
    ({"keywords": ["x"], "pause_seconds": [10]}, "pause_seconds"),
    ({"keywords": ["x"], "max_result": 5}, "Unknown study setting"),
    ({"keywords": ["x"], "match_mode": "fuzzy"}, "match_mode"),
    ({"keywords": [f"k{i}" for i in range(MAX_SEARCHES + 1)]}, "limit per batch"),
])
def test_study_rejects(data, message):
    with pytest.raises(ValueError, match=message):
        Study.from_dict(data)


def test_bad_study_file(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Can't read study file"):
        Study.load(f)


def _fake_search(results):
    calls = []

    async def search(opts, progress):
        calls.append((opts.keyword, opts.group_url, opts.batch_id))
        await progress("saved 1/5", 1, 5)
        return results[len(calls) - 1]
    return search, calls


def test_run_batch_runs_everything_and_writes_report(tmp_path):
    study = Study.from_dict({"keywords": ["a", "b"], "groups": ["https://www.facebook.com/groups/1/"],
                             "output_dir": str(tmp_path)})
    ok = {"ok": True, "status": "completed", "run_dir": "x", "stats": {"saved": 1}, "dataset": {"ok": True}}
    failed = {"ok": False, "status": "failed", "error": {"code": "unexpected", "message": "boom"}}
    search, calls = _fake_search([ok, failed, ok, ok])
    slept, messages = [], []

    async def sleep(s):
        slept.append(s)

    async def progress(message, done, total):
        messages.append(message)

    result = asyncio.run(run_batch(study, search, progress, sleep))
    assert result["ok"] and result["status"] == "completed"
    assert result["searches_done"] == 4 and len(calls) == 4 and len(slept) == 3
    assert [c[:2] for c in calls] == [("a", None), ("a", "https://www.facebook.com/groups/1/"),
                                      ("b", None), ("b", "https://www.facebook.com/groups/1/")]
    assert len({c[2] for c in calls}) == 1 and calls[0][2] == result["batch_id"]
    assert result["runs"][1]["error"] == "unexpected" and result["runs"][1]["message"] == "boom"
    assert any(m.startswith("[2/4] saved") for m in messages)

    report = json.loads(open(result["report_file"], encoding="utf-8").read())
    assert report["status"] == "completed" and report["study"]["keywords"] == ["a", "b"]


@pytest.mark.parametrize("error", [{"ok": False, "error": "not_logged_in", "message": "Not logged in."},
                                   {"ok": False, "status": "stopped", "error": {"code": "checkpoint", "message": "cp"}}])
def test_run_batch_stops_on_account_problems(tmp_path, error):
    study = Study.from_dict({"keywords": ["a", "b", "c"], "output_dir": str(tmp_path)})
    ok = {"ok": True, "status": "completed"}
    search, calls = _fake_search([ok, error, ok])

    async def sleep(s):
        pass

    result = asyncio.run(run_batch(study, search, None, sleep))
    assert not result["ok"] and result["status"] == "stopped"
    assert len(calls) == 2 and result["searches_done"] == 2
    assert "skipped" in result["stopped_reason"]
