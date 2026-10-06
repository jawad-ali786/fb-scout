"""Local multilingual sentiment model (Phase 3, method "model"): offline, on the CPU, sentiment only.

Default model: cardiffnlp/twitter-xlm-roberta-base-sentiment, XLM-RoBERTa base (pretrained on
100 languages, Urdu included) fine-tuned for negative / neutral / positive on tweets in eight
languages. It's the comparison method for the thesis. It judges the overall tone of a text,
not the sentiment towards the keyword, and it doesn't read Roman Urdu particularly well:
both are worth measuring against the gold set rather than assuming.

Needs the optional `ml` extra (`uv sync --extra ml` in plugins/fb-scout/server): PyTorch (CPU)
and transformers, plus the model itself (about 1.1 GB, downloaded once into the Hugging Face cache).
"""

from __future__ import annotations

from typing import Any, Callable

from .dataset import Dataset

DEFAULT_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
# Models that only say LABEL_0/1/2 use this order (cardiffnlp's).
INDEX_LABELS = ("negative", "neutral", "positive")
ALIASES = {"neg": "negative", "neu": "neutral", "pos": "positive"}

Progress = Callable[[str, int, int], Any] | None


class ModelUnavailable(Exception):
    """The model can't run (packages missing). The message says how to fix it."""


def load_pipeline(model_name: str = DEFAULT_MODEL):
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise ModelUnavailable("The local model needs PyTorch and transformers: in plugins/fb-scout/server run "
                               "`uv sync --extra ml` (about 1.3 GB with the model, downloaded once).") from exc
    return pipeline("text-classification", model=model_name, tokenizer=model_name, device=-1)


def to_sentiment(label: str) -> str:
    name = (label or "").strip().lower()
    if name.startswith("label_") and name[6:].isdigit() and int(name[6:]) < len(INDEX_LABELS):
        return INDEX_LABELS[int(name[6:])]
    name = ALIASES.get(name, name)
    if name not in INDEX_LABELS:
        raise ValueError(f"unexpected model label {label!r}")
    return name


def model_text(item: dict, max_chars: int = 2000) -> str:
    """What the model reads: the text, or Facebook's image text when there is no text."""
    text = (item.get("text") or "").strip() or (item.get("image_text") or "").strip()
    return text[:max_chars] or "(empty)"


def label_with_model(ds: Dataset, items: list[dict], model_name: str = DEFAULT_MODEL, batch_size: int = 16,
                     progress: Progress = None, pipe=None) -> dict:
    """Label items with the local model; saves after every batch."""
    pipe = pipe or load_pipeline(model_name)
    done, errors = 0, []
    for start in range(0, len(items), batch_size):
        chunk = items[start:start + batch_size]
        outputs = pipe([model_text(it) for it in chunk], batch_size=batch_size, truncation=True, max_length=512)
        entries = []
        for item, out in zip(chunk, outputs):
            top = out[0] if isinstance(out, list) else out
            try:
                sentiment = to_sentiment(top["label"])
            except ValueError as exc:
                errors.append(f"{item['item_id']}: {exc}")
                continue
            entries.append({"item_id": item["item_id"], "keyword": item["keyword"], "sentiment": sentiment,
                            "reason": f"model score {float(top['score']):.2f}"})
        saved = ds.annotate(entries, method="model", model=model_name)
        done += saved["labeled"]
        errors.extend(saved["errors"])
        if progress:
            progress(f"labeled {done}/{len(items)}", min(start + batch_size, len(items)), len(items))
    return {"method": "model", "model": model_name, "labeled": done, "errors": errors}
