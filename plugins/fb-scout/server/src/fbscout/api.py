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
        return {"ok": True, **ds.stats(keyword)}


def dataset_items(output_dir: str | None = None, limit: int = 50, offset: int = 0, full_text: bool = False,
                  **filters) -> dict:
    ds, error = _open_dataset(output_dir)
    if error:
        return error
    root = _root(output_dir).resolve()
    limit, offset = max(1, min(500, int(limit))), max(0, int(offset))
    with ds:
        rows = ds.items(limit=limit, offset=offset, text_chars=None if full_text else 400, **filters)
        total = ds.count(**filters)
    for r in rows:
        r.pop("url_key", None)
        r.pop("content_key", None)
        r["screenshot_file"] = str(root / r["screenshot_path"]) if r.get("screenshot_path") else None
    return {"ok": True, "total": total, "returned": len(rows), "offset": offset, "items": rows}


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
