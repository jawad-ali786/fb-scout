"""Gold set and evaluation (Phase 3).

  sample_sheet       a random (or stratified) sample of items for people to label, as a CSV that
                     opens in Excel, without any model labels (blind), plus an instructions file
  import_sheet       a filled sheet -> annotations by that annotator ("A", "B", ... or "gold")
  agreement          Cohen's kappa between two annotators
  adjudication_sheet the items two annotators disagree on, for a third person to decide ("gold")
  evaluate           every method against the reference: the "gold" annotator, otherwise the
                     items where A and B agree, or another method ("agent", "human:A", ...)
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .analysis import ASPECTS, CHURN, FEEDBACK_TYPES, METHODS, RUBRIC, RUBRIC_VERSION, SENTIMENTS
from .analysis import normalize as normalize_annotation
from .dataset import Dataset, _primary_per_keyword, format_aspects

LABEL_COLUMNS = ["sentiment", "aspects", "churn", "churn_target", "feedback_type", "notes"]
SHEET_COLUMNS = ["sample_id", "item_id", "keyword", "kind", "language", "text", "image_text", "url",
                 "screenshot_file"] + LABEL_COLUMNS
CATEGORICAL = {"sentiment": SENTIMENTS, "churn": CHURN, "feedback_type": FEEDBACK_TYPES}


# ---- sheets -----------------------------------------------------------------

def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def instructions_text() -> str:
    return (f"# Labeling instructions (rubric version {RUBRIC_VERSION})\n\n"
            "Fill the columns sentiment, aspects, churn, churn_target and feedback_type for every row. "
            "Leave a row's sentiment empty to skip it. Label on your own, without looking at other people's "
            "or the AI's labels.\n\n"
            f"- sentiment: {' / '.join(SENTIMENTS)}\n"
            f"- aspects: 'aspect:sentiment' pairs separated by ';', e.g. 'price:negative; warranty:negative'. "
            f"Empty when there is no opinion. Aspects: {', '.join(ASPECTS)}\n"
            f"- churn: {' / '.join(CHURN)}; churn_target: the brand they switch to (or empty)\n"
            f"- feedback_type: {' / '.join(FEEDBACK_TYPES)}\n\n## Rubric\n\n{RUBRIC}\n")


def sample_sheet(ds: Dataset, out_file: Path, n: int = 300, keyword: str | None = None, seed: int = 42,
                 stratify: bool = False, output_root: Path | None = None, exclude_annotated: bool = True) -> dict:
    """Write a blind labeling sheet (CSV, Excel-friendly) and its instructions next to it.

    stratify=True draws the same number of items per primary sentiment label (so negatives aren't
    swamped by ads); the sampling method is written into the instructions file for the methods section.
    """
    pool = ds.label_queue(method="human", annotator="__sampling__", keyword=keyword, limit=None, text_chars=None)
    pairs = pool["to_label"]
    if exclude_annotated:
        done = {(a["item_id"], a["keyword"].lower()) for a in ds.annotations(method="human")}
        pairs = [p for p in pairs if (p["item_id"], p["keyword"].lower()) not in done]
    rng = random.Random(seed)
    if stratify:
        primary = {}
        for item in ds.items(limit=None, text_chars=10):
            for a in _primary_per_keyword(item["annotations"]):
                primary[(item["item_id"], a["keyword"].lower())] = a["sentiment"]
        strata: dict[str, list] = {}
        for p in pairs:
            strata.setdefault(primary.get((p["item_id"], p["keyword"].lower()), "unlabeled"), []).append(p)
        per = max(1, n // max(1, len(strata)))
        chosen = []
        for name in sorted(strata):
            group = strata[name]
            chosen += rng.sample(group, min(per, len(group)))
        rest = [p for p in pairs if p not in chosen]
        chosen += rng.sample(rest, min(max(0, n - len(chosen)), len(rest)))
        rng.shuffle(chosen)
        method = f"stratified by primary sentiment label ({', '.join(f'{k}: {len(v)} available' for k, v in sorted(strata.items()))})"
    else:
        chosen = rng.sample(pairs, min(n, len(pairs)))
        method = "simple random sample"
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    root = Path(output_root).resolve() if output_root else ds.path.parent.resolve()
    with out_file.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SHEET_COLUMNS)
        w.writeheader()
        for i, p in enumerate(chosen, 1):
            w.writerow({"sample_id": i, "item_id": p["item_id"], "keyword": p["keyword"], "kind": p["kind"],
                        "language": p["language"], "text": p["text"], "image_text": p["image_text"], "url": p["url"],
                        "screenshot_file": str(root / p["screenshot_path"]) if p.get("screenshot_path") else ""})
    instructions = out_file.with_name(out_file.stem + "_instructions.md")
    instructions.write_text(instructions_text() + f"\n## Sample\n\n{len(chosen)} items, {method}, seed {seed}, "
                            f"keyword: {keyword or 'all'}, drawn {_stamp()} UTC.\n", encoding="utf-8")
    return {"file": str(out_file), "instructions": str(instructions), "rows": len(chosen), "available": len(pairs),
            "sampling": method, "seed": seed}


def import_sheet(ds: Dataset, file: Path, annotator: str) -> dict:
    """Read a filled sheet; rows with an empty sentiment are skipped."""
    annotator = (annotator or "").strip()
    if not annotator:
        raise ValueError("Say who labeled the sheet (annotator), e.g. A, B or gold.")
    entries, errors, skipped = [], [], 0
    with Path(file).open(encoding="utf-8-sig", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            if not (row.get("sentiment") or "").strip():
                skipped += 1
                continue
            entry = {k: row.get(k) for k in ("item_id", "keyword", "sentiment", "aspects", "churn", "churn_target",
                                             "feedback_type")}
            entry["reason"] = row.get("notes")
            try:
                normalize_annotation(entry)
            except ValueError as exc:
                errors.append(f"row {line} ({row.get('item_id')}): {exc}")
                continue
            entries.append(entry)
    result = ds.annotate(entries, method="human", annotator=annotator)
    return {"annotator": annotator, "imported": result["labeled"], "skipped_empty": skipped,
            "errors": errors + result["errors"]}


# ---- metrics ----------------------------------------------------------------

def cohen_kappa(a: list, b: list) -> float | None:
    if not a or len(a) != len(b):
        return None
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if pe == 1:
        return 1.0 if po == 1 else 0.0
    return round((po - pe) / (1 - pe), 3)


def classification_report(gold: list, pred: list, labels: tuple) -> dict:
    """Accuracy, per-class precision/recall/F1/support, macro F1 and the confusion matrix.

    Like scikit-learn's defaults: macro F1 averages over the classes that occur in the reference or
    the predictions, and an undefined F1 (a class never predicted, or never in the reference) counts as 0.
    """
    n = len(gold)
    per_class, f1s = {}, []
    for c in labels:
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        prec = tp / (tp + fp) if tp + fp else None
        rec = tp / (tp + fn) if tp + fn else None
        f1 = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if (tp + fp) or (tp + fn) else None)
        support = tp + fn
        per_class[c] = {"precision": _r(prec), "recall": _r(rec), "f1": _r(f1), "support": support,
                        "predicted": tp + fp}
        if tp + fp or tp + fn:
            f1s.append(f1 or 0.0)
    confusion = {g: {p: sum(x == g and y == p for x, y in zip(gold, pred)) for p in labels} for g in labels}
    return {"n": n, "accuracy": _r(sum(g == p for g, p in zip(gold, pred)) / n) if n else None,
            "macro_f1": _r(sum(f1s) / len(f1s)) if f1s else None, "per_class": per_class,
            "confusion (rows = reference)": confusion}


def aspect_report(gold: list[set], pred: list[set]) -> dict:
    """Which aspects an item is about (multi-label): precision/recall/F1 per aspect and micro-averaged."""
    out, tp_all, fp_all, fn_all = {}, 0, 0, 0
    for aspect in ASPECTS:
        tp = sum(aspect in g and aspect in p for g, p in zip(gold, pred))
        fp = sum(aspect not in g and aspect in p for g, p in zip(gold, pred))
        fn = sum(aspect in g and aspect not in p for g, p in zip(gold, pred))
        tp_all, fp_all, fn_all = tp_all + tp, fp_all + fp, fn_all + fn
        if tp + fp + fn:
            prec = tp / (tp + fp) if tp + fp else None
            rec = tp / (tp + fn) if tp + fn else None
            f1 = 2 * prec * rec / (prec + rec) if prec and rec else 0.0
            out[aspect] = {"precision": _r(prec), "recall": _r(rec), "f1": _r(f1), "support": tp + fn}
    prec = tp_all / (tp_all + fp_all) if tp_all + fp_all else None
    rec = tp_all / (tp_all + fn_all) if tp_all + fn_all else None
    micro = 2 * prec * rec / (prec + rec) if prec and rec else (0.0 if prec is not None and rec is not None else None)
    return {"n": len(gold), "micro": {"precision": _r(prec), "recall": _r(rec), "f1": _r(micro)}, "per_aspect": out}


def _r(x) -> float | None:
    return None if x is None else round(x, 3)


# ---- reference and evaluation ----------------------------------------------------

def _by_pair(rows: list[dict]) -> dict[tuple, dict]:
    return {(r["item_id"], r["keyword"].lower()): r for r in rows}


def _method_rows(ds: Dataset, spec: str, keyword: str | None) -> dict[tuple, dict]:
    name, _, annotator = spec.partition(":")
    if name not in METHODS:
        raise ValueError(f"unknown method {spec!r}: use {', '.join(METHODS)} (human:<annotator> for a person)")
    if name == "human":
        return _by_pair(ds.annotations(method="human", annotator=annotator or "gold", keyword=keyword))
    return _by_pair(ds.annotations(method=name, keyword=keyword))


def agreement(ds: Dataset, a: str = "A", b: str = "B", keyword: str | None = None) -> dict:
    """Cohen's kappa between two annotators on the items both labeled."""
    ra, rb = _method_rows(ds, f"human:{a}", keyword), _method_rows(ds, f"human:{b}", keyword)
    both = sorted(set(ra) & set(rb))
    out = {"annotators": [a, b], "items_both_labeled": len(both)}
    for field in CATEGORICAL:
        pairs = [(ra[k][field], rb[k][field]) for k in both if ra[k][field] and rb[k][field]]
        out[field] = {"n": len(pairs), "kappa": cohen_kappa([x for x, _ in pairs], [y for _, y in pairs]),
                      "raw_agreement": _r(sum(x == y for x, y in pairs) / len(pairs)) if pairs else None}
    aspect_kappa = {}
    with_aspects = [k for k in both if ra[k]["aspects"] is not None and rb[k]["aspects"] is not None]
    for aspect in ASPECTS:
        x = [aspect in {i["aspect"] for i in ra[k]["aspects"]} for k in with_aspects]
        y = [aspect in {i["aspect"] for i in rb[k]["aspects"]} for k in with_aspects]
        if any(x) or any(y):
            aspect_kappa[aspect] = cohen_kappa(x, y)
    out["aspects (presence)"] = {"n": len(with_aspects), "kappa": aspect_kappa}
    return out


def _disagree(x: dict, y: dict) -> list[str]:
    fields = [f for f in CATEGORICAL if x.get(f) and y.get(f) and x[f] != y[f]]
    if x.get("aspects") is not None and y.get("aspects") is not None and \
            {i["aspect"] for i in x["aspects"]} != {i["aspect"] for i in y["aspects"]}:
        fields.append("aspects")
    return fields


def adjudication_sheet(ds: Dataset, out_file: Path, a: str = "A", b: str = "B", keyword: str | None = None) -> dict:
    """The items A and B disagree on, both labels side by side, with empty final columns."""
    ra, rb = _method_rows(ds, f"human:{a}", keyword), _method_rows(ds, f"human:{b}", keyword)
    keys = [k for k in sorted(set(ra) & set(rb)) if _disagree(ra[k], rb[k])]
    texts = {i["item_id"]: i for i in ds.items(limit=None, text_chars=None)}
    columns = ["item_id", "keyword", "text", "disagree_on"]
    for who in (a, b):
        columns += [f"{who}_{f}" for f in ("sentiment", "aspects", "churn", "feedback_type")]
    columns += LABEL_COLUMNS
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for k in keys:
            row = {"item_id": k[0], "keyword": ra[k]["keyword"], "text": (texts.get(k[0]) or {}).get("text"),
                   "disagree_on": ", ".join(_disagree(ra[k], rb[k]))}
            for who, src in ((a, ra[k]), (b, rb[k])):
                row.update({f"{who}_sentiment": src["sentiment"], f"{who}_aspects": format_aspects(src["aspects"]),
                            f"{who}_churn": src["churn"], f"{who}_feedback_type": src["feedback_type"]})
            w.writerow(row)
    return {"file": str(out_file), "disagreements": len(keys), "items_both_labeled": len(set(ra) & set(rb)),
            "next_step": "Fill the final columns (sentiment, aspects, ...) and import the sheet as annotator 'gold'."}


def reference_rows(ds: Dataset, reference: str = "gold", keyword: str | None = None) -> tuple[dict, str]:
    """The labels to compare against, and a description of where they come from."""
    if reference != "gold":
        return _method_rows(ds, reference, keyword), reference
    gold = _method_rows(ds, "human:gold", keyword)
    ra, rb = _method_rows(ds, "human:A", keyword), _method_rows(ds, "human:B", keyword)
    agreed = {}
    for k in set(ra) & set(rb):
        if k in gold:
            continue
        row = {"item_id": ra[k]["item_id"], "keyword": ra[k]["keyword"], "aspects": None}
        for field in CATEGORICAL:
            row[field] = ra[k][field] if ra[k][field] == rb[k][field] else None
        if ra[k]["aspects"] is not None and rb[k]["aspects"] is not None and \
                {i["aspect"] for i in ra[k]["aspects"]} == {i["aspect"] for i in rb[k]["aspects"]}:
            row["aspects"] = ra[k]["aspects"]
        agreed[k] = row
    source = f"human:gold ({len(gold)} items)" + (f" + items where A and B agree ({len(agreed)})" if agreed else "")
    return {**agreed, **gold}, source


def evaluate(ds: Dataset, reference: str = "gold", methods: list[str] | None = None,
             keyword: str | None = None) -> dict:
    """Compare each method with the reference on the items both labeled."""
    ref, source = reference_rows(ds, reference, keyword)
    if not ref:
        raise ValueError("No reference labels yet: import a labeled sheet as annotator 'gold' (or A and B), "
                         "or compare with another method, e.g. reference='agent'.")
    if methods is None:
        present = {a["method"] for a in ds.annotations() if a["method"] != "human"}
        methods = [m for m in ("claude-api", "agent", "model") if m in present]
    results = {}
    for spec in methods:
        if spec == reference:
            continue
        pred = _method_rows(ds, spec, keyword)
        common = sorted(set(ref) & set(pred))
        res: dict = {"items_compared": len(common)}
        for field, labels in CATEGORICAL.items():
            pairs = [(ref[k][field], pred[k][field]) for k in common if ref[k].get(field) and pred[k].get(field)]
            if pairs:
                res[field] = classification_report([g for g, _ in pairs], [p for _, p in pairs], labels)
                res[field]["kappa_vs_reference"] = cohen_kappa([g for g, _ in pairs], [p for _, p in pairs])
        asp = [k for k in common if ref[k].get("aspects") is not None and pred[k].get("aspects") is not None]
        if asp:
            res["aspects"] = aspect_report([{i["aspect"] for i in ref[k]["aspects"]} for k in asp],
                                           [{i["aspect"] for i in pred[k]["aspects"]} for k in asp])
        results[spec] = res
    out = {"reference": source, "keyword": keyword, "rubric_version": RUBRIC_VERSION, "methods": results,
           "evaluated_at": _stamp()}
    if _method_rows(ds, "human:A", keyword) and _method_rows(ds, "human:B", keyword):
        out["inter_annotator_agreement"] = agreement(ds, "A", "B", keyword)
    return out


def report_markdown(result: dict) -> str:
    lines = [f"# FB Scout evaluation ({result['evaluated_at']} UTC)", "",
             f"Reference: {result['reference']}. Keyword: {result['keyword'] or 'all'}. "
             f"Rubric version {result['rubric_version']}.", ""]
    for spec, res in result["methods"].items():
        lines += [f"## {spec}", "", f"Items compared: {res['items_compared']}", ""]
        for field in CATEGORICAL:
            if field not in res:
                continue
            r = res[field]
            lines += [f"**{field}**: accuracy {r['accuracy']}, macro-F1 {r['macro_f1']}, "
                      f"kappa {r['kappa_vs_reference']} (n = {r['n']})", "",
                      "| class | precision | recall | F1 | in reference | predicted |", "|---|---|---|---|---|---|"]
            lines += [f"| {c} | {_d(v['precision'])} | {_d(v['recall'])} | {_d(v['f1'])} | {v['support']} | "
                      f"{v['predicted']} |" for c, v in r["per_class"].items() if v["support"] or v["predicted"]]
            lines.append("")
        if "aspects" in res:
            m = res["aspects"]["micro"]
            lines += [f"**aspects** (which aspects, n = {res['aspects']['n']}): micro precision {m['precision']}, "
                      f"recall {m['recall']}, F1 {m['f1']}", ""]
    if "inter_annotator_agreement" in result:
        ia = result["inter_annotator_agreement"]
        lines += ["## Inter-annotator agreement (A vs B)", "",
                  f"Items labeled by both: {ia['items_both_labeled']}", ""]
        lines += [f"- {f}: kappa {ia[f]['kappa']}, raw agreement {ia[f]['raw_agreement']} (n = {ia[f]['n']})"
                  for f in CATEGORICAL]
        lines.append("")
    return "\n".join(lines)


def _d(x) -> str:
    return "–" if x is None else str(x)


def save_report(result: dict, reports_dir: Path) -> dict:
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = reports_dir / f"evaluation_{result['evaluated_at']}"
    stem.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    stem.with_suffix(".md").write_text(report_markdown(result), encoding="utf-8")
    return {"json": str(stem.with_suffix(".json")), "markdown": str(stem.with_suffix(".md"))}
