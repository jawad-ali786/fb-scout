"""High-level API shared by the MCP server and the CLI."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from . import __version__
from . import login as login_methods
from .batch import Study, run_batch
from .browser import is_logged_in, open_browser
from .config import dataset_path, default_output_root, home_dir
from .dataset import Dataset
from .errors import Busy, FBScoutError
from .export import default_export_path, export_items
from .scraper import Progress, SearchOptions, run_search
from .storage import RESULTS_FILE, list_runs

# One browser run at a time (the profile can only be opened once).
_lock = asyncio.Lock()


async def _exclusive(coro):
    if _lock.locked():
        coro.close()
        return Busy().to_dict()
    async with _lock:
        return await coro


def _root(output_dir: str | None) -> Path:
    return Path(output_dir) if output_dir else default_output_root()


async def _status() -> dict:
    try:
        async with open_browser(headless=True) as session:
            logged_in = await is_logged_in(session.context)
            channel = session.channel
    except FBScoutError as exc:
        return exc.to_dict()
    return {
        "ok": True,
        "version": __version__,
        "logged_in": logged_in,
        "browser": channel,
        "profile_dir": str(home_dir()),
        "default_output_dir": str(default_output_root().resolve()),
        "dataset": str(dataset_path(default_output_root()).resolve()),
        "next_step": None if logged_in else (
            "Call fb_login: method 'browser' opens a normal Chrome window to log in (then close it); "
            "'firefox' copies an existing Facebook login from Firefox; 'cookie_file' imports an exported cookie file."),
    }


async def status() -> dict:
    return await _exclusive(_status())


async def login(method: str = "browser", cookie_file: str | None = None, timeout_seconds: int = 600,
                force: bool = False) -> dict:
    return await _exclusive(login_methods.login(method, cookie_file, timeout_seconds, force))


# ---- searching --------------------------------------------------------------

def import_run(run_dir: str | Path, output_dir: str | None = None) -> dict:
    """Add one run to the dataset. Never raises: a failed import must not fail the search."""
    root = _root(output_dir)
    path = dataset_path(root)
    try:
        with Dataset(path) as ds:
            return {"ok": True, "file": str(path.resolve()), **ds.import_run(run_dir, root)}
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        return {"ok": False, "file": str(path), "error": str(exc)[:300],
                "hint": "The run's results.json is fine. Run fb_import_runs (CLI: fbscout db import) to retry."}


async def _search_and_import(opts: SearchOptions, progress: Progress = None) -> dict:
    try:
        result = await run_search(opts, progress)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    if result.get("run_dir"):
        result["dataset"] = import_run(result["run_dir"], opts.output_dir)
        if opts.only_negative and result.get("run_id"):
            result["next_step"] = (
                "The user wants only negative posts. Label every item of this run: call fb_label_queue with "
                f"run_id='{result['run_id']}', read each text, and save labels (negative / neutral / positive with a "
                "short reason) with fb_label_items; repeat until nothing remains. Then report only "
                f"fb_dataset_items(run_id='{result['run_id']}', sentiment='negative').")
    return result


async def search(opts: SearchOptions, progress: Progress = None) -> dict:
    return await _exclusive(_search_and_import(opts, progress))


async def batch(study: Study, progress: Progress = None) -> dict:
    async def _batch():
        result = await run_batch(study, _search_and_import, progress)
        stats = dataset_stats(study.output_dir)
        if stats.get("ok"):
            result["dataset_totals"] = {k: stats[k] for k in ("dataset", "runs", "items", "by_keyword")}
        return result
    return await _exclusive(_batch())


def runs(output_dir: str | None = None, keyword: str | None = None, limit: int = 20) -> dict:
    root = _root(output_dir)
    return {"ok": True, "output_dir": str(root.resolve()), "runs": list_runs(root, keyword, limit)}


# ---- dataset ----------------------------------------------------------------

def _open_dataset(output_dir: str | None) -> tuple[Dataset | None, dict | None]:
    """The dataset for this output folder. Created on first use from the runs already there."""
    root = _root(output_dir)
    path = dataset_path(root)
    if not path.exists():
        if not any(root.glob(f"*/*/{RESULTS_FILE}")):
            return None, {"ok": False, "error": "no_dataset", "message": f"No runs or dataset in {root.resolve()} yet.",
                          "hint": "Run a search first (fb_search)."}
        ds = Dataset(path)
        ds.import_all(root)
        return ds, None
    return Dataset(path), None


def dataset_import(output_dir: str | None = None) -> dict:
    root = _root(output_dir)
    with Dataset(dataset_path(root)) as ds:
        result = ds.import_all(root)
        return {"ok": not result["errors"], "dataset": str(ds.path.resolve()), **result, "items_total": ds.count()}


def dataset_stats(output_dir: str | None = None, keyword: str | None = None) -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    with ds:
        return {"ok": True, **ds.stats(keyword), "analysis": ds.label_stats(keyword=keyword)}


def analysis_stats(output_dir: str | None = None, keyword: str | None = None, method: str | None = None,
                   **filters) -> dict:
    """Sentiment, aspects, churn, feedback types and sentiment per month, from the primary labels (or one method)."""
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    try:
        with ds:
            return {"ok": True, **ds.label_stats(keyword=keyword, method=method, **filters)}
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}


def dataset_items(output_dir: str | None = None, limit: int = 50, offset: int = 0, full_text: bool = False,
                  **filters) -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    root = _root(output_dir).resolve()
    limit, offset = max(1, min(500, int(limit))), max(0, int(offset))
    try:
        with ds:
            rows = ds.items(limit=limit, offset=offset, text_chars=None if full_text else 400, **filters)
            total = ds.count(**filters)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    for r in rows:
        r.pop("url_key", None)
        r.pop("content_key", None)
        r["screenshot_file"] = str(root / r["screenshot_path"]) if r.get("screenshot_path") else None
    return {"ok": True, "total": total, "returned": len(rows), "offset": offset, "items": rows}


def dataset_exclude(item_ids: list[str], reason: str, output_dir: str | None = None) -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    try:
        with ds:
            result = ds.exclude_items(item_ids, reason)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    return {"ok": not result["not_found"], **result}


def label_queue(output_dir: str | None = None, keyword: str | None = None, run_id: str | None = None,
                batch_id: str | None = None, limit: int = 20, method: str = "agent") -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    root = _root(output_dir).resolve()
    with ds:
        result = ds.label_queue(method=method, keyword=keyword, run_id=run_id, batch_id=batch_id,
                                limit=max(1, min(50, int(limit))))
    for item in result["to_label"]:
        item["screenshot_file"] = str(root / item["screenshot_path"]) if item.get("screenshot_path") else None
    return {"ok": True, **result}


def label_items(labels: list[dict], output_dir: str | None = None, labeler: str = "agent") -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    with ds:
        result = ds.label_items(labels, labeler)
    return {"ok": not result["errors"], **result}


def annotate(entries: list[dict], output_dir: str | None = None, method: str = "agent", annotator: str = "",
             model: str | None = None) -> dict:
    """Save annotations (sentiment, aspects, churn, feedback type) from an agent or a person."""
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    try:
        with ds:
            result = ds.annotate(entries, method=method, annotator=annotator, model=model)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    return {"ok": not result["errors"], **result}


# ---- Phase 3: automatic labeling, gold set, evaluation ----------------------------

# Rough cost per item for the estimate before a Claude API run (rubric cached; text, JSON and thinking vary).
_EST_TOKENS = {"low": 600, "medium": 1100, "high": 2200, "xhigh": 3500, "max": 5000}


def _estimate(items: list[dict], model: str, effort: str, batch: bool) -> dict:
    from . import claude_labeler as cl
    prices = cl.PRICES.get(model)
    if not prices:
        return {"items": len(items), "estimated_cost_usd": None}
    text_tokens = sum(len(it.get("text") or "") for it in items) / 3.5 + 120 * len(items)
    out_tokens = _EST_TOKENS.get(effort, 1100) * len(items)
    rubric = 1900 * prices[3] * len(items) + 1900 * prices[2]
    usd = (text_tokens * prices[0] + out_tokens * prices[1] + rubric) / 1_000_000 * (0.5 if batch else 1)
    return {"items": len(items), "estimated_cost_usd": round(usd, 2),
            "estimate_note": "rough: real thinking and output length vary; the run reports the actual cost"}


def analyze(method: str, output_dir: str | None = None, keyword: str | None = None, run_id: str | None = None,
            batch_id: str | None = None, limit: int | None = 100, mode: str = "sync", model: str | None = None,
            effort: str | None = None, dry_run: bool = False, progress=None) -> dict:
    """Label the items that `method` ("claude-api" or "model") hasn't labeled yet."""
    from . import claude_labeler as cl
    from . import local_model as lm

    if method not in ("claude-api", "model"):
        return {"ok": False, "error": "invalid_argument",
                "message": "method must be 'claude-api' or 'model' (agents label through fb_label_items)"}
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    with ds:
        queue = ds.label_queue(method=method, keyword=keyword, run_id=run_id, batch_id=batch_id,
                               limit=limit, text_chars=None)
        items = queue["to_label"]
        base = {"method": method, "to_label": len(items), "not_labeled_in_total": queue["remaining"]}
        if not items:
            return {"ok": True, **base, "labeled": 0, "message": "Nothing left to label for this method."}
        try:
            if method == "model":
                name = model or lm.DEFAULT_MODEL
                if dry_run:
                    return {"ok": True, "dry_run": True, **base, "model": name}
                return {"ok": True, **base, **lm.label_with_model(ds, items, name, progress=progress)}
            name, eff = model or cl.DEFAULT_MODEL, effort or cl.DEFAULT_EFFORT
            if mode not in ("sync", "batch"):
                raise ValueError("mode must be 'sync' or 'batch'")
            if dry_run:
                return {"ok": True, "dry_run": True, **base, "model": name, "effort": eff, "mode": mode,
                        **_estimate(items, name, eff, mode == "batch")}
            if mode == "batch":
                state_dir = _root(output_dir).resolve() / "_claude_batches"
                return {"ok": True, **base, **cl.submit_batch(ds, items, state_dir, name, eff),
                        "next_step": "Collect the labels later with collect_batch (fbscout analyze --collect <id>)."}
            result = cl.label_sync(ds, items, name, eff, progress=progress)
            return {"ok": not result["errors"], **base, **result}
        except (cl.LabelerUnavailable, lm.ModelUnavailable) as exc:
            return {"ok": False, "error": "unavailable", "message": str(exc)}
        except ValueError as exc:
            return {"ok": False, "error": "invalid_argument", "message": str(exc)}


def collect_batch(batch_id: str, output_dir: str | None = None, wait_minutes: float = 0) -> dict:
    from . import claude_labeler as cl
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    try:
        with ds:
            result = cl.collect_batch(ds, batch_id, _root(output_dir).resolve() / "_claude_batches", wait_minutes)
    except cl.LabelerUnavailable as exc:
        return {"ok": False, "error": "unavailable", "message": str(exc)}
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    return {"ok": not result.get("errors"), **result}


def gold_sample(output_dir: str | None = None, n: int = 300, keyword: str | None = None, seed: int = 42,
                stratify: bool = False, file: str | None = None) -> dict:
    from . import evaluation as ev
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    root = _root(output_dir).resolve()
    target = Path(file) if file else root / "_gold" / f"gold_sample_{ev._stamp()}.csv"
    with ds:
        return {"ok": True, **ev.sample_sheet(ds, target, n, keyword, seed, stratify, root),
                "next_step": "Give the sheet and its instructions to two people; import each filled sheet "
                             "with annotator A and B (gold_import)."}


def gold_import(file: str, annotator: str, output_dir: str | None = None) -> dict:
    from . import evaluation as ev
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    try:
        with ds:
            result = ev.import_sheet(ds, Path(file), annotator)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    return {"ok": not result["errors"], **result}


def gold_adjudication(output_dir: str | None = None, a: str = "A", b: str = "B", keyword: str | None = None,
                      file: str | None = None) -> dict:
    from . import evaluation as ev
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    root = _root(output_dir).resolve()
    target = Path(file) if file else root / "_gold" / f"adjudication_{a}_{b}_{ev._stamp()}.csv"
    with ds:
        return {"ok": True, **ev.adjudication_sheet(ds, target, a, b, keyword)}


def evaluate(output_dir: str | None = None, reference: str = "gold", methods: list[str] | None = None,
             keyword: str | None = None, save: bool = True) -> dict:
    from . import evaluation as ev
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    try:
        with ds:
            result = ev.evaluate(ds, reference, methods, keyword)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
    files = ev.save_report(result, _root(output_dir).resolve() / "_reports") if save else {}
    return {"ok": True, **result, "report_files": files}


def dataset_export(output_dir: str | None = None, fmt: str = "csv", out_file: str | None = None,
                   anonymize: bool = False, **filters) -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    target = Path(out_file) if out_file else default_export_path(_root(output_dir).resolve(), fmt,
                                                                 filters.get("keyword"), anonymize)
    try:
        with ds:
            return export_items(ds, target, fmt, anonymize, **filters)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_argument", "message": str(exc)}
