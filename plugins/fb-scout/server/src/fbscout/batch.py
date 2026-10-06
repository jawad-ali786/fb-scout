"""Studies: several keywords × (global search + a fixed list of groups), run one after another.

A study file (JSON) describes the research sample:

    {
      "name": "solar-brands",
      "keywords": ["solar panel", "Brand X"],
      "groups": ["https://www.facebook.com/groups/123456/", "https://www.facebook.com/groups/solarpk/"],
      "include_global": true,
      "max_results": 20
    }

Searches run strictly one at a time with a long random pause in between
(pause_seconds, scaled by FBSCOUT_PACE). The batch stops at the first
checkpoint / block / login problem. Every finished search is imported into the
dataset, and a batch report is kept in <output root>/_batches/.
To repeat a study on a schedule, use the operating system's scheduler (see README).
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from .config import default_output_root, pace_factor
from .matching import MODES, clean_keyword
from .scraper import Progress, SearchOptions
from .storage import slugify, utc_now_iso
from .urls import group_root

# Errors after which the next search would fail too (or put the account at risk).
STOP_CODES = frozenset({"checkpoint", "blocked", "not_logged_in", "profile_in_use", "browser_unavailable"})
MAX_SEARCHES = 50

SearchFn = Callable[[SearchOptions, Progress], Awaitable[dict]]
SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class Study:
    keywords: list[str]
    name: str = "study"
    groups: list[str] = field(default_factory=list)
    include_global: bool = True
    max_results: int = 20
    match_mode: str = "phrase"
    include_comments: bool = False
    max_comment_posts: int = 5
    max_minutes_per_search: float = 10
    pause_seconds: list[float] = field(default_factory=lambda: [60, 180])
    blur_names: bool = False
    headless: bool = True
    output_dir: str | None = None
    include_marketplace: bool = False         # also one Marketplace search per keyword
    marketplace_location: str | None = None
    listing_details: bool = False
    include_name_matches: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "Study":
        known = [f.name for f in fields(cls)]
        unknown = sorted(set(data) - set(known))
        if unknown:
            raise ValueError(f"Unknown study setting(s): {', '.join(unknown)}. Allowed: {', '.join(known)}.")
        if "keywords" not in data:
            raise ValueError("A study needs \"keywords\": a list of keywords to search.")
        study = cls(**data)
        study.validate()
        return study

    @classmethod
    def load(cls, path: Path | str) -> "Study":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Can't read study file {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("A study file must contain a JSON object (see README for an example).")
        data.setdefault("name", Path(path).stem)
        return cls.from_dict(data)

    def validate(self) -> None:
        if isinstance(self.keywords, str):
            self.keywords = [self.keywords]
        if isinstance(self.groups, str):
            self.groups = [self.groups]
        unique: dict[str, str] = {}   # search is case-insensitive: "Solar Panel" = "solar panel"
        for k in (clean_keyword(str(k)) for k in self.keywords):
            if k:
                unique.setdefault(k.lower(), k)
        self.keywords = list(unique.values())
        if not self.keywords:
            raise ValueError("A study needs at least one keyword.")
        roots = []
        for g in self.groups:
            root = group_root(str(g))
            if not root:
                raise ValueError(f"Not a Facebook group URL: {g!r}")
            roots.append(root)
        self.groups = list(dict.fromkeys(roots))
        if not self.include_global and not self.groups and not self.include_marketplace:
            raise ValueError("Nothing to search: include_global and include_marketplace are false and the study "
                             "has no groups.")
        if self.match_mode not in MODES:
            raise ValueError(f"match_mode must be one of {MODES}")
        pauses = list(self.pause_seconds) if isinstance(self.pause_seconds, (list, tuple)) else []
        if len(pauses) != 2 or not all(isinstance(p, (int, float)) and p >= 0 for p in pauses) or pauses[0] > pauses[1]:
            raise ValueError("pause_seconds must be [min, max] seconds, e.g. [60, 180].")
        self.pause_seconds = [float(p) for p in pauses]
        n = len(self.plan())
        if n > MAX_SEARCHES:
            raise ValueError(f"This study needs {n} searches; the limit per batch is {MAX_SEARCHES}. "
                             "Split it into several study files and run them on different days.")

    def plan(self, batch_id: str | None = None) -> list[SearchOptions]:
        # (source, group_url) per search: global posts, each group, then Marketplace.
        scopes: list[tuple[str, str | None]] = (
            ([("posts", None)] if self.include_global else []) + [("posts", g) for g in self.groups]
            + ([("marketplace", None)] if self.include_marketplace else []))
        return [
            SearchOptions(
                keyword=k, group_url=g, source=src, max_results=self.max_results, match_mode=self.match_mode,
                include_comments=self.include_comments and src == "posts", max_comment_posts=self.max_comment_posts,
                output_dir=self.output_dir, headless=self.headless, max_minutes=self.max_minutes_per_search,
                blur_names=self.blur_names, batch_id=batch_id, marketplace_location=self.marketplace_location,
                listing_details=self.listing_details, include_name_matches=self.include_name_matches,
            )
            for k in self.keywords for src, g in scopes
        ]

    def describe(self) -> dict:
        plan = self.plan()
        per_search = min(self.max_minutes_per_search, self.max_results * 15 / 60 + 1)
        pause = sum(self.pause_seconds) / 2 / 60 * pace_factor()
        return {
            "name": self.name,
            "searches": len(plan),
            "plan": [{"keyword": o.keyword, "scope": _where(o)} for o in plan],
            "estimated_minutes": round(len(plan) * per_search + max(0, len(plan) - 1) * pause),
        }


def _where(opts: SearchOptions) -> str:
    if opts.source == "marketplace":
        return "Marketplace" + (f" ({opts.marketplace_location})" if opts.marketplace_location else "")
    return opts.group_url or "global search"


def _error_code(result: dict) -> str | None:
    """Run summaries carry {"error": {"code": ...}}; errors before a run starts carry {"error": "<code>"}."""
    err = result.get("error")
    if isinstance(err, dict):
        return err.get("code")
    return err if isinstance(err, str) else None


def _error_message(result: dict) -> str | None:
    err = result.get("error")
    return err.get("message") if isinstance(err, dict) else result.get("message")


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_batch(study: Study, search: SearchFn, progress: Progress = None,
                    sleep: SleepFn = asyncio.sleep) -> dict:
    output_root = Path(study.output_dir) if study.output_dir else default_output_root()
    started = datetime.now(timezone.utc)
    batch_id = f"{slugify(study.name)}_{started:%Y%m%d-%H%M%S}"
    report_path = output_root.expanduser().resolve() / "_batches" / f"{batch_id}.json"
    plan = study.plan(batch_id)
    total = len(plan)
    report = {
        "batch_id": batch_id, "study": asdict(study), "started_at": utc_now_iso(), "finished_at": None,
        "status": "running", "searches_planned": total, "searches_done": 0, "stopped_reason": None, "runs": [],
    }
    _write(report_path, report)

    async def say(message: str, done: int) -> None:
        if progress:
            await progress(message, done, total)

    for i, opts in enumerate(plan):
        if i:
            delay = random.uniform(*study.pause_seconds) * pace_factor()
            await say(f"waiting {delay:.0f}s before search {i + 1}/{total}", i)
            await sleep(delay)
        where = _where(opts)
        await say(f"search {i + 1}/{total}: {opts.keyword!r} in {where}", i)

        async def sub(message: str, done: int, of: int, _i: int = i) -> None:
            await say(f"[{_i + 1}/{total}] {message}", _i)

        result = await search(opts, sub)
        code = _error_code(result)
        report["runs"].append({
            "keyword": opts.keyword,
            "scope": opts.scope,
            "group_url": opts.group_url,
            "status": result.get("status") or ("completed" if result.get("ok") else "failed"),
            "error": code,
            "message": _error_message(result),
            "run_dir": result.get("run_dir"),
            "stats": result.get("stats"),
            "dataset": result.get("dataset"),
        })
        report["searches_done"] = i + 1
        _write(report_path, report)
        if code in STOP_CODES:
            report["stopped_reason"] = f"{code}: {_error_message(result) or code} Remaining searches were skipped."
            break

    report["status"] = "stopped" if report["stopped_reason"] else "completed"
    report["finished_at"] = utc_now_iso()
    _write(report_path, report)
    summary = {k: v for k, v in report.items() if k != "study"}
    return {"ok": report["status"] == "completed", "report_file": str(report_path), **summary}
