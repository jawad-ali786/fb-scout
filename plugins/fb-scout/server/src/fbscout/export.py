"""Export the dataset for analysis tools: CSV (Excel-friendly), JSONL, or Parquet.

One row per item (distinct post/comment). With anonymize=True, authors become
stable pseudonyms (author_id) and every URL is dropped, because profile and
permalink URLs contain user ids. Names inside the post text and inside
screenshots are NOT removed (use blur_names when collecting for the latter).
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import anonymization_salt
from .dataset import Dataset
from .storage import slugify

FORMATS = ("csv", "jsonl", "parquet")

COLUMNS = [
    "item_id", "kind", "keywords", "language", "posted_at", "posted_date", "posted_at_precision",
    "author_name", "author_url", "group_name", "group_url", "post_url", "comment_url", "parent_post_url",
    "text", "image_text", "time_text", "time_exact", "first_seen", "last_seen", "times_seen",
    "screenshot_path", "first_run_id",
]
URL_COLUMNS = ("author_url", "post_url", "comment_url", "parent_post_url")
ANON_COLUMNS = [c for c in COLUMNS if c not in ("author_name", *URL_COLUMNS)]
ANON_COLUMNS.insert(ANON_COLUMNS.index("group_name"), "author_id")


def pseudonym(identity: str | None, salt: bytes) -> str | None:
    if not identity:
        return None
    return "a_" + hmac.new(salt, identity.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def default_export_path(output_root: Path, fmt: str, keyword: str | None, anonymize: bool) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    name = f"fbscout_{slugify(keyword) if keyword else 'all'}_{stamp}{'_anon' if anonymize else ''}.{fmt}"
    return Path(output_root) / "_exports" / name


def export_items(ds: Dataset, path: Path | str, fmt: str = "csv", anonymize: bool = False, **filters) -> dict:
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    rows = ds.items(limit=None, text_chars=None, **filters)
    columns = COLUMNS
    if anonymize:
        salt = anonymization_salt()
        for r in rows:
            r["author_id"] = pseudonym(r.get("author_url") or r.get("author_name"), salt)
        columns = ANON_COLUMNS
    out = [{c: r.get(c) for c in columns} for r in rows]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        # utf-8-sig so Excel shows Urdu and emoji correctly.
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=columns)
            w.writeheader()
            for r in out:
                w.writerow({**r, "keywords": " | ".join(r.get("keywords") or [])})
    elif fmt == "jsonl":
        with path.open("w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ValueError("Parquet export needs pyarrow: in the server folder run `uv sync --extra parquet`, "
                             "or export as csv/jsonl.") from exc
        pq.write_table(pa.Table.from_pylist(out), str(path))

    warnings = []
    if anonymize:
        warnings.append("Names inside the post text and in screenshots are not removed. "
                        "Don't share screenshots unless the runs used blur_names.")
    return {"ok": True, "file": str(path), "format": fmt, "rows": len(out), "anonymized": anonymize,
            "columns": columns, "warnings": warnings}
