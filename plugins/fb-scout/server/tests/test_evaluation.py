import csv

import pytest

from fbscout import api
from fbscout import evaluation as ev
from fbscout import local_model as lm
from fbscout.dataset import Dataset
from test_dataset import make_run, rec

TEXTS = {
    "a": "Brand X warranty claim rejected twice, worst service, switching to Brand Y",
    "b": "Brand X inverter sale, best price in Lahore",
    "c": "Brand X has been perfect for 2 years, highly recommend",
    "d": "Brand X ka battery backup bekar hai, 1 ghanta bhi nahi chalti",
    "e": "Does Brand X support lithium batteries?",
    "f": "Brand X installation team was late but the inverter works great",
}


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("FBSCOUT_HOME", str(tmp_path / "home"))
    r = tmp_path / "out"
    make_run(r, "brand-x", "20261001-100000", [
        rec(k, text=t, post_url=f"https://www.facebook.com/reel/{n}/", kind="reel", keyword="Brand X")
        for n, (k, t) in enumerate(TEXTS.items(), 1)], keyword="Brand X")
    with Dataset(r / "fbscout.sqlite") as ds:
        ds.import_all(r)
    return r


def ids(root) -> dict:
    with Dataset(root / "fbscout.sqlite") as ds:
        return {next(k for k, t in TEXTS.items() if t == i["text"]): i["item_id"] for i in ds.items(limit=None, text_chars=None)}


def test_kappa_and_report_known_values():
    assert ev.cohen_kappa([1, 1, 0, 0], [1, 0, 0, 0]) == 0.5
    assert ev.cohen_kappa(["a", "b"], ["a", "b"]) == 1.0
    assert ev.cohen_kappa(["a", "a"], ["a", "a"]) == 1.0 and ev.cohen_kappa([], []) is None
    rep = ev.classification_report(["negative", "negative", "neutral", "positive"],
                                   ["negative", "neutral", "neutral", "positive"], ("negative", "neutral", "positive"))
    assert rep["accuracy"] == 0.75
    assert rep["per_class"]["negative"] == {"precision": 1.0, "recall": 0.5, "f1": 0.667, "support": 2, "predicted": 1}
    assert rep["per_class"]["neutral"]["precision"] == 0.5 and rep["macro_f1"] == round((0.667 + 0.667 + 1) / 3, 3)
    assert rep["confusion (rows = reference)"]["negative"]["neutral"] == 1
    # A class only predicted (never in the reference) counts in macro-F1 with F1 = 0, as in scikit-learn.
    wrong = ev.classification_report(["neutral", "neutral"], ["positive", "neutral"], ("negative", "neutral", "positive"))
    assert wrong["per_class"]["positive"] == {"precision": 0.0, "recall": None, "f1": 0.0, "support": 0, "predicted": 1}
    assert wrong["macro_f1"] == round((2 / 3 + 0) / 2, 3)
    asp = ev.aspect_report([{"price"}, {"warranty", "customer_service"}, set()], [{"price"}, {"warranty"}, {"delivery"}])
    assert asp["micro"] == {"precision": 0.667, "recall": 0.667, "f1": 0.667}
    assert asp["per_aspect"]["customer_service"]["recall"] == 0.0


def test_local_model_labels_with_a_simulated_pipeline(root):
    def pipe(texts, **kw):
        assert kw["truncation"] is True and kw["max_length"] == 512
        return [[{"label": "LABEL_0" if ("worst" in t or "bekar" in t) else "Positive", "score": 0.91}] for t in texts]

    with Dataset(root / "fbscout.sqlite") as ds:
        items = ds.label_queue(method="model", limit=None, text_chars=None)["to_label"]
        out = lm.label_with_model(ds, items, "test-model", batch_size=4, pipe=pipe)
        assert out["labeled"] == 6 and out["errors"] == []
        assert ds.count(sentiment="negative", method="model") == 2
        rows = ds.annotations(method="model")
        assert rows[0]["model"] == "test-model" and rows[0]["reason"] == "model score 0.91"
        assert rows[0]["aspects"] is None                  # sentiment only
    assert lm.to_sentiment("LABEL_1") == "neutral" and lm.to_sentiment("NEG") == "negative"
    with pytest.raises(ValueError):
        lm.to_sentiment("LABEL_7")


def _fill(sheet, labels: dict, out):
    rows = list(csv.DictReader(open(sheet, encoding="utf-8-sig")))
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            r.update(labels.get(r["item_id"], {}))
            w.writerow(r)


def test_gold_workflow_end_to_end(root, tmp_path):
    i = ids(root)
    out = str(root)
    sample = api.gold_sample(out, n=6, keyword="Brand X", seed=1)
    assert sample["ok"] and sample["rows"] == 6 and sample["file"].endswith(".csv")
    sheet = sample["file"]
    header = next(csv.reader(open(sheet, encoding="utf-8-sig")))
    assert header == ev.SHEET_COLUMNS and "agent" not in open(sheet, encoding="utf-8-sig").read()   # blind
    assert "rubric version 2" in open(sample["instructions"], encoding="utf-8").read()

    base = {i["a"]: dict(sentiment="negative", aspects="warranty:negative; customer_service:negative", churn="switched",
                         churn_target="Brand Y", feedback_type="complaint"),
            i["b"]: dict(sentiment="neutral", aspects="", churn="none", feedback_type="advertisement"),
            i["c"]: dict(sentiment="positive", aspects="durability:positive", churn="none", feedback_type="praise"),
            i["d"]: dict(sentiment="negative", aspects="performance:negative", churn="none", feedback_type="complaint"),
            i["e"]: dict(sentiment="neutral", aspects="", churn="none", feedback_type="question")}
    b_labels = {**base, i["d"]: dict(sentiment="negative", aspects="durability:negative", churn="none",
                                     feedback_type="defect_report"),
                i["e"]: dict(sentiment="neutral", aspects="", churn="none", feedback_type="question")}
    _fill(sheet, base, tmp_path / "A.csv")
    _fill(sheet, b_labels, tmp_path / "B.csv")
    _fill(sheet, {i["f"]: dict(sentiment="furious")}, tmp_path / "bad.csv")
    imp = api.gold_import(str(tmp_path / "A.csv"), "A", out)
    assert imp["imported"] == 5 and imp["skipped_empty"] == 1          # f left empty
    assert api.gold_import(str(tmp_path / "B.csv"), "B", out)["imported"] == 5
    bad = api.gold_import(str(tmp_path / "bad.csv"), "C", out)
    assert not bad["ok"] and "row" in bad["errors"][0]

    adj = api.gold_adjudication(out, "A", "B", file=str(tmp_path / "adj.csv"))
    assert adj["disagreements"] == 1                                    # only d (aspects + feedback type)
    adj_rows = list(csv.DictReader(open(tmp_path / "adj.csv", encoding="utf-8-sig")))
    assert adj_rows[0]["item_id"] == i["d"] and "feedback_type" in adj_rows[0]["disagree_on"]
    assert adj_rows[0]["A_feedback_type"] == "complaint" and adj_rows[0]["B_feedback_type"] == "defect_report"

    # An agent labels everything; one sentiment mistake (b) and one missed aspect (a).
    agent = [{"item_id": i["a"], "sentiment": "negative", "aspects": "warranty:negative", "churn": "switched",
              "churn_target": "Brand Y", "feedback_type": "complaint"},
             {"item_id": i["b"], "sentiment": "positive", "aspects": "price:positive", "churn": "none",
              "feedback_type": "advertisement"},
             {"item_id": i["c"], "sentiment": "positive", "aspects": "durability:positive", "churn": "none",
              "feedback_type": "praise"},
             {"item_id": i["d"], "sentiment": "negative", "aspects": "performance:negative", "churn": "none",
              "feedback_type": "complaint"},
             {"item_id": i["e"], "sentiment": "neutral", "aspects": "", "churn": "none", "feedback_type": "question"},
             {"item_id": i["f"], "sentiment": "positive", "aspects": "installation:negative; performance:positive",
              "churn": "none", "feedback_type": "praise"}]
    assert api.annotate(agent, out, method="agent")["labeled"] == 6

    res = api.evaluate(out)
    assert res["ok"] and "A and B agree (5)" in res["reference"]
    agent_res = res["methods"]["agent"]
    assert agent_res["items_compared"] == 5                              # f has no reference
    assert agent_res["sentiment"]["accuracy"] == 0.8                     # b wrong
    assert agent_res["sentiment"]["per_class"]["negative"]["recall"] == 1.0
    assert agent_res["churn"]["accuracy"] == 1.0
    assert agent_res["feedback_type"]["n"] == 4                          # d: A and B disagree, no reference
    assert res["inter_annotator_agreement"]["sentiment"]["kappa"] == 1.0
    assert res["inter_annotator_agreement"]["feedback_type"]["kappa"] < 1.0
    md = open(res["report_files"]["markdown"], encoding="utf-8").read()
    assert "## agent" in md and "Inter-annotator agreement" in md

    gold_d = [{"item_id": i["d"], "sentiment": "negative", "aspects": "durability:negative", "churn": "none",
               "feedback_type": "defect_report"}]
    api.annotate(gold_d, out, method="human", annotator="gold")
    res2 = api.evaluate(out, save=False)
    assert res2["methods"]["agent"]["feedback_type"]["n"] == 5          # d now has a gold label
    vs_agent = api.evaluate(out, reference="agent", methods=["human:A"], save=False)
    assert vs_agent["methods"]["human:A"]["items_compared"] == 5
    assert api.evaluate(out, reference="robot")["error"] == "invalid_argument"


def test_stratified_sample_balances_labels(root):
    i = ids(root)
    out = str(root)
    api.annotate([{"item_id": i[k], "sentiment": s} for k, s in
                  {"a": "negative", "b": "neutral", "c": "positive", "d": "negative", "e": "neutral",
                   "f": "positive"}.items()], out, method="agent")
    sample = api.gold_sample(out, n=3, stratify=True, seed=3)
    rows = list(csv.DictReader(open(sample["file"], encoding="utf-8-sig")))
    sentiments = {next(s for k, s in {"a": "negative", "b": "neutral", "c": "positive", "d": "negative",
                                      "e": "neutral", "f": "positive"}.items() if i[k] == r["item_id"]) for r in rows}
    assert sentiments == {"negative", "neutral", "positive"} and "stratified" in sample["sampling"]


def test_analyze_dry_run_and_missing_packages(root, monkeypatch):
    out = str(root)
    est = api.analyze("claude-api", out, dry_run=True, effort="low")
    assert est["ok"] and est["dry_run"] and est["items"] == 6 and est["estimated_cost_usd"] > 0
    batch_est = api.analyze("claude-api", out, dry_run=True, effort="low", mode="batch")
    assert batch_est["estimated_cost_usd"] == pytest.approx(est["estimated_cost_usd"] / 2, abs=0.01)
    assert api.analyze("robots", out)["error"] == "invalid_argument"

    def no_transformers(name):
        raise lm.ModelUnavailable("needs uv sync --extra ml")
    monkeypatch.setattr(lm, "load_pipeline", no_transformers)
    missing = api.analyze("model", out)
    assert missing["error"] == "unavailable" and "--extra ml" in missing["message"]
