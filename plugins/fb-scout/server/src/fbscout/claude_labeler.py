"""Label items with a fixed Claude model, prompt and settings (Phase 3, method "claude-api").

The same rubric and schema as every other method (analysis.py); the model, effort, rubric
version and the model that actually answered are stored with each label, so a run can be
described exactly in a methods section and repeated.

Needs the optional `claude` extra (`uv sync --extra claude` in plugins/fb-scout/server) and
Anthropic credentials (ANTHROPIC_API_KEY, or a profile from `ant auth login`).

  sync   one request per item, now; a request declined by a safety classifier is retried
         server-side on Anthropic's recommended fallback model (recorded as the label's model)
  batch  Message Batches API: half the price, results usually within an hour (max. 24 h);
         no server-side fallback there, a declined item is reported as an error
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .analysis import RUBRIC, RUBRIC_VERSION, SCHEMA, item_prompt
from .dataset import Dataset

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_TOKENS = 4096                     # the JSON is short; the rest is room for the model's thinking
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# US$ per million tokens: input, output, cache write (5 min), cache read. Batches cost half.
PRICES = {"claude-opus-5-5": (4.00, 20.00, 5.00, 0.20)}

Progress = Callable[[str, int, int], Any] | None


class LabelerUnavailable(Exception):
    """The run can't start (package missing, no credentials). The message says how to fix it."""


class LabelError(Exception):
    """One item couldn't be labeled."""


def _anthropic():
    try:
        import anthropic
    except ImportError as exc:
        raise LabelerUnavailable("The Claude API labeler needs the anthropic package: in plugins/fb-scout/server "
                                 "run `uv sync --extra claude`.") from exc
    return anthropic


def make_client():
    return _anthropic().Anthropic()


def request_params(item: dict, model: str, effort: str) -> dict:
    """One Messages API request: the cached rubric, the item, and the JSON schema the answer must follow."""
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": RUBRIC, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{"role": "user", "content": item_prompt(item["keyword"], item)}],
    }


def parse_message(message) -> dict:
    """The label from a response, or LabelError."""
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise LabelError(f"declined by Claude ({getattr(details, 'category', None) or 'no category'})")
    if message.stop_reason == "max_tokens":
        raise LabelError("the answer was cut off (max_tokens)")
    text = next((b.text for b in message.content if b.type == "text"), None)
    if not text:
        raise LabelError("the response had no text")
    try:
        return json.loads(text)
    except ValueError as exc:
        raise LabelError("the response was not valid JSON") from exc


def _add_usage(totals: dict, usage) -> None:
    for field in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
        totals[field] = totals.get(field, 0) + (getattr(usage, field, None) or 0)


def cost_usd(totals: dict, model: str, batch: bool = False) -> float | None:
    if model not in PRICES:
        return None
    p_in, p_out, p_write, p_read = PRICES[model]
    cost = (totals.get("input_tokens", 0) * p_in + totals.get("output_tokens", 0) * p_out
            + totals.get("cache_creation_input_tokens", 0) * p_write
            + totals.get("cache_read_input_tokens", 0) * p_read) / 1_000_000
    return round(cost * (0.5 if batch else 1.0), 4)


def _settings(model: str, effort: str) -> dict:
    if effort not in EFFORTS:
        raise ValueError(f"effort must be one of {', '.join(EFFORTS)}")
    return {"model": model, "effort": effort, "rubric_version": RUBRIC_VERSION, "max_tokens": MAX_TOKENS}


def label_sync(ds: Dataset, items: list[dict], model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
               progress: Progress = None, client=None) -> dict:
    """Label items one request at a time; each label is saved as soon as it arrives."""
    settings = _settings(model, effort)
    anthropic = _anthropic()
    client = client or make_client()
    totals: dict = {}
    done, errors, served_by = 0, [], {}
    for i, item in enumerate(items):
        ref = f"{item['item_id']} ({item['keyword']})"
        try:
            message = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default",
                                                  **request_params(item, model, effort))
        except anthropic.AuthenticationError as exc:
            raise LabelerUnavailable("No valid Anthropic credentials: set ANTHROPIC_API_KEY (or run "
                                     "`ant auth login`) and try again.") from exc
        except (anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise LabelerUnavailable(f"The API refused the request: {exc.message}") from exc
        except anthropic.BadRequestError as exc:
            errors.append(f"{ref}: {exc.message}")
            continue
        except anthropic.RateLimitError:
            errors.append(f"{ref}: rate limited after retries; stopped here, run again to continue")
            break
        except anthropic.APIStatusError as exc:
            errors.append(f"{ref}: API error {exc.status_code}")
            continue
        except anthropic.APIConnectionError:
            errors.append(f"{ref}: network error; stopped here, run again to continue")
            break
        _add_usage(totals, message.usage)
        try:
            label = parse_message(message)
        except LabelError as exc:
            errors.append(f"{ref}: {exc}")
            continue
        saved = ds.annotate([{**label, "item_id": item["item_id"], "keyword": item["keyword"]}],
                            method="claude-api", model=message.model)
        errors.extend(saved["errors"])
        done += saved["labeled"]
        served_by[message.model] = served_by.get(message.model, 0) + 1
        if progress:
            progress(f"labeled {done}/{len(items)}", i + 1, len(items))
    return {"mode": "sync", **settings, "labeled": done, "errors": errors, "served_by": served_by,
            "usage": totals, "cost_usd": cost_usd(totals, model)}


def submit_batch(ds: Dataset, items: list[dict], state_dir: Path, model: str = DEFAULT_MODEL,
                 effort: str = DEFAULT_EFFORT, client=None) -> dict:
    """Send all items as one Message Batch. The mapping back to items is kept in state_dir."""
    settings = _settings(model, effort)
    _anthropic()
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = client or make_client()
    refs = {f"r{i}": {"item_id": it["item_id"], "keyword": it["keyword"]} for i, it in enumerate(items)}
    requests = [Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**request_params(items[i], model, effort)))
                for i, cid in enumerate(refs)]
    batch = client.messages.batches.create(requests=requests)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"{batch.id}.json"
    state = {"batch_id": batch.id, **settings, "created_at": datetime.now(timezone.utc).isoformat(),
             "dataset": str(ds.path), "collected": False, "items": refs}
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"mode": "batch", **settings, "batch_id": batch.id, "requests": len(requests),
            "state_file": str(state_file), "status": batch.processing_status}


def collect_batch(ds: Dataset, batch_id: str, state_dir: Path, wait_minutes: float = 0, client=None,
                  sleep: Callable[[float], None] = time.sleep) -> dict:
    """Save a batch's labels into the dataset. Waits up to wait_minutes for it to finish."""
    state_file = state_dir / f"{batch_id}.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"No submitted batch {batch_id} in {state_dir}") from exc
    _anthropic()
    client = client or make_client()
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status == "ended":
            break
        if time.monotonic() >= deadline:
            counts = batch.request_counts
            return {"mode": "batch", "batch_id": batch_id, "status": batch.processing_status,
                    "processing": counts.processing, "succeeded": counts.succeeded, "errored": counts.errored,
                    "hint": "Not finished yet; collect it again later (usually within an hour, at most 24 hours)."}
        sleep(30)

    totals: dict = {}
    done, errors, served_by = 0, [], {}
    for result in client.messages.batches.results(batch_id):
        ref = state["items"].get(result.custom_id)
        if not ref:
            continue
        name = f"{ref['item_id']} ({ref['keyword']})"
        if result.result.type != "succeeded":
            errors.append(f"{name}: {result.result.type}")
            continue
        message = result.result.message
        _add_usage(totals, message.usage)
        try:
            label = parse_message(message)
        except LabelError as exc:
            errors.append(f"{name}: {exc}")
            continue
        saved = ds.annotate([{**label, **ref}], method="claude-api", model=message.model)
        errors.extend(saved["errors"])
        done += saved["labeled"]
        served_by[message.model] = served_by.get(message.model, 0) + 1
    state["collected"] = True
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"mode": "batch", "batch_id": batch_id, "status": "ended", "model": state["model"],
            "effort": state["effort"], "rubric_version": state["rubric_version"], "labeled": done, "errors": errors,
            "served_by": served_by, "usage": totals, "cost_usd": cost_usd(totals, state["model"], batch=True)}
