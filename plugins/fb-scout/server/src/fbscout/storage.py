"""Run folders and results.json.

Layout:  <output_root>/<keyword-slug>/<YYYYMMDD-HHMMSS>/results.json
                                                     /screenshots/*.png
                                                     /debug/        (only if needed)
results.json is rewritten after every record, so a crash keeps partial data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import __version__

RESULTS_FILE = "results.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(text: str, max_len: int = 60) -> str:
    s = unicodedata.normalize("NFKC", text).strip().lower()
    s = re.sub(r"[^\w]+", "-", s).strip("-_")
    return s[:max_len].strip("-") or "keyword"


def record_id(key: str) -> str:
    return "r_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


class RunWriter:
    def __init__(self, output_root: Path | str, keyword: str, params: dict):
        started = datetime.now(timezone.utc)
        slug = slugify(keyword)
        stamp = started.strftime("%Y%m%d-%H%M%S")
        base = Path(output_root).expanduser().resolve() / slug
        self.dir = base / stamp
        n = 1
        while self.dir.exists():
            self.dir = base / f"{stamp}-{n}"
            n += 1
        self.screenshots_dir = self.dir / "screenshots"
        self.screenshots_dir.mkdir(parents=True)

        self.results: list[dict] = []
        self.meta: dict = {
            "tool": "fb-scout",
            "version": __version__,
            "run_id": f"{slug}_{self.dir.name}",
            "keyword": keyword,
            **params,
            "started_at": utc_now_iso(),
            "finished_at": None,
            "status": "running",
            "error": None,
            "stats": {
                "candidates_seen": 0,
                "skipped_not_posts": 0,
                "verified": 0,
                "saved": 0,
                "screenshots": 0,
                "comment_posts_scanned": 0,
                "comments_saved": 0,
                "errors": 0,
            },
            "warnings": [],
        }
        self.flush()

    @property
    def stats(self) -> dict:
        return self.meta["stats"]

    def warn(self, message: str) -> None:
        if message not in self.meta["warnings"]:
            self.meta["warnings"].append(message)

    def screenshot_target(self, rank: int, kind: str, rid: str) -> tuple[str, Path]:
        name = f"{rank:03d}_{kind}_{rid[2:10]}.png"
        return name, self.screenshots_dir / name

    def debug_dir(self) -> Path:
        d = self.dir / "debug"
        d.mkdir(exist_ok=True)
        return d

    def add(self, record: dict) -> None:
        self.results.append(record)
        self.stats["saved"] = len(self.results)
        self.flush()

    def finish(self, status: str, error: dict | None = None) -> None:
        self.meta["status"] = status
        self.meta["error"] = error
        self.meta["finished_at"] = utc_now_iso()
        self.flush()

    def flush(self) -> None:
        data = json.dumps({"run": self.meta, "results": self.results}, ensure_ascii=False, indent=2)
        target = self.dir / RESULTS_FILE
        tmp = self.dir / (RESULTS_FILE + ".tmp")
        tmp.write_text(data, encoding="utf-8")
        try:
            os.replace(tmp, target)
        except PermissionError:  # Windows: target open in another program
            target.write_text(data, encoding="utf-8")
            tmp.unlink(missing_ok=True)

    def summary(self, max_records: int = 50) -> dict:
        """Compact view for the agent (no full post text)."""
        compact_keys = (
            "kind", "keyword_verified", "matched_in", "match_snippet", "post_url", "comment_url",
            "author_name", "group_name", "time_text", "time_exact", "posted_date", "language", "screenshot_name",
        )
        return {
            "ok": self.meta["status"] == "completed",
            "status": self.meta["status"],
            "error": self.meta["error"],
            "run_dir": str(self.dir),
            "results_json": str(self.dir / RESULTS_FILE),
            "stats": self.stats,
            "warnings": self.meta["warnings"],
            "records": [{k: r.get(k) for k in compact_keys} for r in self.results[:max_records]],
            "records_truncated": max(0, len(self.results) - max_records),
        }


def list_runs(output_root: Path | str, keyword: str | None = None, limit: int = 20) -> list[dict]:
    root = Path(output_root).expanduser()
    if not root.exists():
        return []
    pattern = f"{slugify(keyword)}/*/{RESULTS_FILE}" if keyword else f"*/*/{RESULTS_FILE}"
    runs = []
    for path in root.glob(pattern):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))["run"]
        except (OSError, ValueError, KeyError):
            continue
        runs.append({
            "run_id": meta.get("run_id"),
            "keyword": meta.get("keyword"),
            "scope": meta.get("scope"),
            "status": meta.get("status"),
            "started_at": meta.get("started_at"),
            "stats": meta.get("stats"),
            "run_dir": str(path.parent),
        })
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs[:limit]
