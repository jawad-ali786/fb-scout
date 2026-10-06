"""The Claude API labeler against a simulated client: the requests it sends and how it handles answers."""

import json
from types import SimpleNamespace as NS

import pytest

from fbscout import claude_labeler as cl
from fbscout.analysis import RUBRIC, SCHEMA
from fbscout.dataset import Dataset
from test_dataset import make_run, rec


def usage(i=100, o=300, cw=0, cr=1900):
    return NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=cw, cache_read_input_tokens=cr)


def answer(label: dict, model="claude-opus-5-5", stop="end_turn"):
    return NS(stop_reason=stop, stop_details=None, model=model, usage=usage(),
              content=[NS(type="thinking", thinking=""), NS(type="text", text=json.dumps(label))])


GOOD = {"sentiment": "negative", "aspects": [{"aspect": "warranty", "sentiment": "negative"}], "churn": "none",
        "churn_target": "", "feedback_type": "complaint", "reason": "'very disappointed with the warranty service'"}


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.sent.append(kwargs)
        return self.replies.pop(0)


@pytest.fixture
def ds(tmp_path, monkeypatch):
    monkeypatch.setenv("FBSCOUT_HOME", str(tmp_path / "home"))
    root = tmp_path / "out"
    make_run(root, "inverex", "20261006-100000", [
        rec(r, text=t, post_url=f"https://www.facebook.com/reel/{n}/", kind="reel", keyword="Inverex")
        for n, (r, t) in enumerate([("a", "Inverex warranty service is the worst"), ("b", "Inverex sale 20% off"),
                                    ("c", "kya inverex acha hai?")], 1)], keyword="Inverex")
    d = Dataset(root / "fbscout.sqlite")
    d.import_all(root)
    yield d
    d.close()


def test_label_sync_requests_and_saves(ds):
    items = ds.label_queue(method="claude-api", text_chars=None)["to_label"]
    by_text = {i["text"][:12]: i for i in items}
    replies = {"Inverex warr": answer(GOOD, model="claude-opus-4-8"),       # served by the fallback model
               "Inverex sale": answer({**GOOD, "sentiment": "neutral", "aspects": [], "feedback_type": "advertisement"}),
               "kya inverex ": NS(stop_reason="refusal", stop_details=NS(category="general_harms"), model="claude-opus-5-5",
                                  usage=usage(o=0), content=[])}
    client = FakeClient([replies[i["text"][:12]] for i in items])
    progress = []
    out = cl.label_sync(ds, items, effort="low", client=client, progress=lambda m, d, t: progress.append(m))

    sent = client.sent[0]
    assert sent["model"] == "claude-opus-5-5" and sent["max_tokens"] == cl.MAX_TOKENS
    assert sent["betas"] == ["server-side-fallback-2026-07-01"] and sent["fallbacks"] == "default"
    assert sent["system"] == [{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}]
    assert sent["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}}
    assert sent["messages"][0]["content"].startswith("KEYWORD: Inverex\n")
    assert "thinking" not in sent                                    # Opus 5.5: always adaptive, never disabled

    assert out["labeled"] == 2 and len(out["errors"]) == 1 and "declined" in out["errors"][0]
    assert out["served_by"] == {"claude-opus-4-8": 1, "claude-opus-5-5": 1}
    assert out["usage"]["cache_read_input_tokens"] == 3 * 1900 and out["cost_usd"] > 0
    assert out["rubric_version"] == "2" and out["effort"] == "low" and progress[-1] == "labeled 2/3"
    saved = {a["item_id"]: a for a in ds.annotations(method="claude-api")}
    complaint = saved[by_text["Inverex warr"]["item_id"]]
    assert complaint["model"] == "claude-opus-4-8" and complaint["feedback_type"] == "complaint"
    assert complaint["aspects"] == [{"aspect": "warranty", "sentiment": "negative"}]
    assert ds.label_queue(method="claude-api")["remaining"] == 1   # the declined one is left for a retry


@pytest.mark.parametrize("message, error", [
    (NS(stop_reason="max_tokens", content=[], stop_details=None), "cut off"),
    (NS(stop_reason="end_turn", content=[NS(type="text", text="not json")], stop_details=None), "not valid JSON"),
    (NS(stop_reason="end_turn", content=[], stop_details=None), "no text"),
])
def test_parse_message_errors(message, error):
    with pytest.raises(cl.LabelError, match=error):
        cl.parse_message(message)


def test_invalid_label_from_the_model_is_an_error_not_a_crash(ds):
    items = ds.label_queue(method="claude-api", limit=1, text_chars=None)["to_label"]
    out = cl.label_sync(ds, items, client=FakeClient([answer({**GOOD, "sentiment": "angry"})]))
    assert out["labeled"] == 0 and "sentiment must be one of" in out["errors"][0]


def test_cost():
    totals = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
              "cache_creation_input_tokens": 1_000_000}
    assert cl.cost_usd(totals, "claude-opus-5-5") == 4 + 20 + 0.2 + 5
    assert cl.cost_usd(totals, "claude-opus-5-5", batch=True) == (4 + 20 + 0.2 + 5) / 2
    assert cl.cost_usd(totals, "some-other-model") is None
    with pytest.raises(ValueError, match="effort"):
        cl._settings("claude-opus-5-5", "turbo")


class FakeBatchClient:
    def __init__(self, label_for):
        self.label_for = label_for
        self.requests = []
        self.polls = 0
        self.messages = NS(batches=NS(create=self._create, retrieve=self._retrieve, results=self._results))

    def _create(self, requests):
        self.requests = requests
        return NS(id="msgbatch_test", processing_status="in_progress")

    def _retrieve(self, batch_id):
        self.polls += 1
        status = "ended" if self.polls >= 2 else "in_progress"
        return NS(processing_status=status, request_counts=NS(processing=3 if status != "ended" else 0,
                                                              succeeded=0, errored=0))

    def _results(self, batch_id):
        for r in self.requests:
            text = r["params"]["messages"][0]["content"]
            if "kya" in text:
                yield NS(custom_id=r["custom_id"], result=NS(type="expired"))
            else:
                yield NS(custom_id=r["custom_id"], result=NS(type="succeeded", message=answer(self.label_for(text))))


def test_batch_submit_and_collect(ds, tmp_path):
    items = ds.label_queue(method="claude-api", text_chars=None)["to_label"]
    client = FakeBatchClient(lambda text: GOOD if "worst" in text else {**GOOD, "sentiment": "neutral", "aspects": []})
    state_dir = tmp_path / "state"
    sub = cl.submit_batch(ds, items, state_dir, effort="medium", client=client)
    assert sub["batch_id"] == "msgbatch_test" and sub["requests"] == 3
    assert "fallbacks" not in client.requests[0]["params"]           # not accepted by the Batches API
    state = json.loads((state_dir / "msgbatch_test.json").read_text(encoding="utf-8"))
    assert len(state["items"]) == 3 and state["collected"] is False

    early = cl.collect_batch(ds, "msgbatch_test", state_dir, wait_minutes=0, client=client)
    assert early["status"] == "in_progress" and "later" in early["hint"]
    done = cl.collect_batch(ds, "msgbatch_test", state_dir, wait_minutes=0, client=client)
    assert done["labeled"] == 2 and len(done["errors"]) == 1 and "expired" in done["errors"][0]
    assert done["cost_usd"] == pytest.approx(cl.cost_usd(done["usage"], "claude-opus-5-5") / 2, abs=1e-4)   # half price
    assert json.loads((state_dir / "msgbatch_test.json").read_text(encoding="utf-8"))["collected"] is True
    with pytest.raises(ValueError, match="No submitted batch"):
        cl.collect_batch(ds, "msgbatch_other", state_dir, client=client)
