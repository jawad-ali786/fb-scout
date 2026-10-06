"""SQLite dataset across runs.

One file per output folder (<output root>/fbscout.sqlite, or FBSCOUT_DB) collects
every run's results.json:

  runs       one row per run: keyword, scope, status, stats
  items      one row per distinct post or comment, deduplicated across runs
  sightings  which run found which item, for which keyword (rank, snippet, screenshot)

Facebook gives the same post different URLs: "pfbid" ids change between
sessions, a multi-photo post is reached through a different photo each time,
and older runs stored group posts as ?multi_permalinks=. So an item is matched on
  url_key      a canonical URL, only when it is stable (numeric ids), or
  content_key  group (or parent post) + a fingerprint of the text, when the
               authors don't contradict each other.
Importing is idempotent: importing the same run again changes nothing.

Records can be excluded (false positives, irrelevant posts): they are listed with
a reason in exclusions.json next to the dataset, and every import skips them.
The run folders are never changed, so they stay the untouched evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from .analysis import METHODS, RUBRIC_VERSION, SENTIMENTS
from .analysis import normalize as normalize_annotation
from .dates import finer, resolve
from .extract import clean_text
from .lang import detect_language
from .matching import normalize
from .storage import RESULTS_FILE, utc_now_iso
from .urls import UI_LABELS, classify_kind, clean_url, group_post_from_photo, group_segment

SCHEMA_VERSION = "3"
EXCLUSIONS_FILE = "exclusions.json"
LABELS_FILE = "labels.json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  keyword TEXT, scope TEXT, group_url TEXT, match_mode TEXT, status TEXT,
  started_at TEXT, finished_at TEXT, run_dir TEXT, tool_version TEXT, batch_id TEXT,
  browser_utc_offset_minutes INTEGER, records INTEGER, stats TEXT, warnings TEXT, imported_at TEXT
);
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,
  url_key TEXT UNIQUE,
  content_key TEXT,
  kind TEXT,
  post_url TEXT, comment_url TEXT, parent_post_url TEXT,
  author_name TEXT, author_url TEXT, group_name TEXT, group_url TEXT,
  text TEXT, image_text TEXT, time_text TEXT, time_exact TEXT,
  price TEXT, location TEXT, condition TEXT,
  posted_at TEXT, posted_date TEXT, posted_at_precision TEXT, posted_at_source TEXT,
  language TEXT,
  screenshot_path TEXT,
  first_seen TEXT, last_seen TEXT, times_seen INTEGER NOT NULL DEFAULT 0,
  first_run_id TEXT, last_run_id TEXT
);
CREATE INDEX IF NOT EXISTS items_content_key ON items(content_key);
CREATE INDEX IF NOT EXISTS items_posted_date ON items(posted_date);
CREATE TABLE IF NOT EXISTS sightings (
  item_id TEXT NOT NULL REFERENCES items(item_id),
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  record_id TEXT NOT NULL,
  keyword TEXT, source TEXT, search_rank INTEGER, kind TEXT,
  keyword_verified INTEGER, matched_in TEXT, matched_terms TEXT, match_snippet TEXT,
  screenshot_path TEXT, captured_at TEXT,
  PRIMARY KEY (item_id, run_id, record_id)
);
CREATE INDEX IF NOT EXISTS sightings_keyword ON sightings(keyword);
CREATE TABLE IF NOT EXISTS annotations (
  item_id TEXT NOT NULL, keyword TEXT NOT NULL COLLATE NOCASE,
  method TEXT NOT NULL, annotator TEXT NOT NULL DEFAULT '',
  sentiment TEXT NOT NULL, aspects TEXT, churn TEXT, churn_target TEXT, feedback_type TEXT, reason TEXT,
  model TEXT, rubric_version TEXT, labeled_at TEXT,
  PRIMARY KEY (item_id, keyword, method, annotator)
);
"""

# Most specific first: a later sighting can upgrade "photo"/"unknown" to "group_post".
KIND_RANK = ("group_post", "post", "reel", "video", "event", "marketplace", "comment", "reply", "photo",
             "profile", "unknown")

# Columns added after the first version: (name, type) for existing dataset files.
ADDED_COLUMNS = (("price", "TEXT"), ("location", "TEXT"), ("condition", "TEXT"))

# Filled in from a later sighting when an item doesn't have them yet.
FILL_FIELDS = ("post_url", "comment_url", "parent_post_url", "author_name", "author_url",
               "group_name", "group_url", "time_text", "time_exact", "image_text", "price", "location", "condition")
TIME_FIELDS = ("posted_at", "posted_date", "posted_at_precision", "posted_at_source")

# pfbid ids and share links are different for every session/share.
_UNSTABLE_URL = re.compile(r"pfbid|/share/", re.I)


def _kind_rank(kind: str | None) -> int:
    return KIND_RANK.index(kind) if kind in KIND_RANK else len(KIND_RANK)


def _comment_id(value: str) -> str:
    """comment_id is numeric, or base64 of 'comment:<post id>_<comment id>'."""
    if value.isdigit():
        return value
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return value
    m = re.fullmatch(r"comment:\d+_(\d+)", decoded)
    return m.group(1) if m else value


def url_key(record: dict) -> str | None:
    """Stable identity from the URL, or None when the URL changes between sessions."""
    if record.get("kind") == "profile":   # a person/page card: the profile itself
        u = clean_url(record.get("post_url") or record.get("author_url"))
        return f"profile:{urlsplit(u).path.lower()}?{urlsplit(u).query}" if u else None
    if record.get("kind") in ("comment", "reply"):
        u = clean_url(record.get("comment_url"))
        if not u:
            return None
        qs = dict(parse_qsl(urlsplit(u).query))
        cid = qs.get("reply_comment_id") or qs.get("comment_id")
        return f"comment:{_comment_id(cid)}" if cid else None
    u = clean_url(record.get("post_url"))
    if not u or _UNSTABLE_URL.search(u) or classify_kind(u) == "unknown":
        return None
    parts = urlsplit(u)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "set"]  # a photo's album param varies
    return "url:" + parts.path.lower() + ("?" + urlencode(query) if query else "")


def _fingerprint(text: str | None) -> str:
    """Letters and digits only (any script), so spacing, emoji and '…' don't matter."""
    return re.sub(r"[\W_]+", "", normalize(text).lower())[:160]


def content_key(record: dict) -> str | None:
    """Identity from place + text; None for texts too short to identify anything.

    The author is not part of the key (older runs sometimes have no author or a
    wrong one); `_same_author` checks it when two records share a key.
    """
    fp = _fingerprint(record.get("text"))
    if len(fp) < 12:
        return None
    is_comment = record.get("kind") in ("comment", "reply")
    where = (group_segment(record.get("group_url")) or "").lower()
    if is_comment:
        where = url_key({"kind": "post", "post_url": record.get("parent_post_url")}) or where
    raw = "|".join(("comment" if is_comment else "post", where, fp))
    return "ck_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _same_author(a: str | None, b: str | None) -> bool:
    a, b = normalize(a).lower(), normalize(b).lower()
    return not a or not b or a == b


def _weak(key: str | None) -> bool:
    """A photo link identifies one photo, not the post (a multi-photo post has many)."""
    return bool(key) and key.startswith(("url:/photo", "url:/photo.php"))


def _same_url_identity(a: str | None, b: str | None) -> bool:
    return a is None or b is None or a == b or _weak(a) or _weak(b)


def _clean_record(rec: dict) -> dict:
    """Fix what older runs stored differently, so all runs merge the same way."""
    rec = dict(rec)
    for k in ("post_url", "comment_url", "parent_post_url", "author_url", "group_url"):
        if rec.get(k):
            rec[k] = clean_url(rec[k]) or rec[k]
    if (rec.get("author_name") or "").strip().lower() in UI_LABELS:  # avatar label, not a name
        rec["author_name"] = rec["author_url"] = None
    if rec.get("text"):
        rec["text"] = clean_text(rec["text"])  # hidden "Facebook" filler lines in older runs
    time_text = rec.get("time_text")
    if time_text and time_text.strip().lower().startswith(("may be", "no photo description")):
        rec["time_text"] = None  # an image description, not a time
    if rec.get("kind") == "photo" and rec.get("group_url"):
        derived = group_post_from_photo(rec.get("post_url"), rec["group_url"])
        if derived:
            rec["post_url"], rec["kind"] = derived, "group_post"
    return rec


def _relative_dir(run_dir: Path, output_root: Path | None) -> str:
    if output_root is not None:
        try:
            return run_dir.resolve().relative_to(Path(output_root).resolve()).as_posix()
        except ValueError:
            pass
    return str(run_dir.resolve())


def _record_id(rec: dict) -> str:
    return rec.get("id") or hashlib.sha1(json.dumps(rec, sort_keys=True).encode()).hexdigest()[:12]


# The label reports use when several methods labeled an item: a person's final ("gold")
# label, then the fixed Claude API run, then an agent, then the local model. Annotators
# other than "gold" (A, B for the agreement check) never decide the reported label.
PRIMARY_SQL = """SELECT * FROM (SELECT a.*, ROW_NUMBER() OVER (PARTITION BY a.item_id, lower(a.keyword) ORDER BY
  CASE WHEN a.method = 'human' THEN 0 WHEN a.method = 'claude-api' THEN 1 WHEN a.method = 'agent' THEN 2
       WHEN a.method = 'model' THEN 3 ELSE 9 END, a.labeled_at DESC) AS rn
  FROM annotations a WHERE NOT (a.method = 'human' AND a.annotator <> 'gold')) WHERE rn = 1"""


def _annotation_row(r: sqlite3.Row) -> dict:
    d = {k: r[k] for k in r.keys() if k != "rn"}
    d["aspects"] = json.loads(d["aspects"]) if d.get("aspects") else ([] if d.get("aspects") == "" else d.get("aspects"))
    return d


def _label_source(method: str | None) -> tuple[str, list]:
    """The annotation rows to filter/count on: the primary label, or one method ("human:A" for an annotator)."""
    if not method:
        return f"({PRIMARY_SQL})", []
    name, _, annotator = method.partition(":")
    if name not in METHODS:
        raise ValueError(f"method must be one of {', '.join(METHODS)} (human:<annotator> for a person)")
    if name == "human":
        return "(SELECT * FROM annotations WHERE method = 'human' AND annotator = ?)", [annotator or "gold"]
    return "(SELECT * FROM annotations WHERE method = ?)", [name]


_RANK = {"human": 0, "claude-api": 1, "agent": 2, "model": 3}


def _primary_per_keyword(annotations: list[dict], method: str | None = None) -> list[dict]:
    """Per keyword, the label reports use (same order as PRIMARY_SQL), or the given method's label."""
    best: dict[str, dict] = {}
    for a in annotations:
        if method:
            name, _, annotator = method.partition(":")
            if a["method"] != name or (name == "human" and a["annotator"] != (annotator or "gold")):
                continue
        elif a["method"] == "human" and a["annotator"] != "gold":
            continue
        k = a["keyword"].lower()
        if k not in best or (_RANK.get(a["method"], 9), -_ts(a)) < (_RANK.get(best[k]["method"], 9), -_ts(best[k])):
            best[k] = a
    return [best[k] for k in sorted(best)]


def _ts(a: dict) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat((a.get("labeled_at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def format_aspects(aspects: list[dict] | None) -> str | None:
    """'price:negative; warranty:negative' (the spreadsheet form)."""
    if aspects is None:
        return None
    return "; ".join(f"{a['aspect']}:{a.get('sentiment') or ''}".rstrip(":") for a in aspects)


def _label_fields(chosen: list[dict]) -> dict:
    """Flat label fields for an item. With several keywords: "Brand X: negative | Brand Y: neutral"."""
    fields = ("sentiment", "reason", "churn", "churn_target", "feedback_type", "method")
    if len(chosen) == 1:
        a = chosen[0]
        out = {f: a.get(f) for f in fields}
        out["aspects"] = a.get("aspects")
    else:
        out = {f: " | ".join(f"{a['keyword']}: {a.get(f)}" for a in chosen if a.get(f)) or None for f in fields}
        joined = " | ".join(f"{a['keyword']}: {format_aspects(a.get('aspects'))}" for a in chosen
                            if a.get("aspects"))
        out["aspects"] = joined or None
    out["sentiment_reason"] = out.pop("reason")
    out["label_method"] = out.pop("method")
    return out


def _split_list(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, list) else str(value).split(",")
    return [v.strip() for v in items if v and v.strip()]


class Dataset:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        have = {r[1] for r in self.conn.execute("PRAGMA table_info(items)")}
        for column, kind in ADDED_COLUMNS:
            if column not in have:
                self.conn.execute(f"ALTER TABLE items ADD COLUMN {column} {kind}")
        self.conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        self.conn.commit()
        self.exclusions_path = self.path.with_name(EXCLUSIONS_FILE)
        self._exclusions = self._load_exclusions()
        self.labels_path = self.path.with_name(LABELS_FILE)
        self._labels_file = self._load_labels_file()
        self._migrate_labels_table()
        self._sync_labels()
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Dataset":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- import -------------------------------------------------------------

    def import_run(self, run_dir: Path | str, output_root: Path | str | None = None) -> dict:
        run_dir = Path(run_dir)
        data = json.loads((run_dir / RESULTS_FILE).read_text(encoding="utf-8"))
        run, results = data.get("run") or {}, data.get("results") or []
        run_id = run.get("run_id") or run_dir.name
        rel_dir = _relative_dir(run_dir, Path(output_root) if output_root else None)
        offset = run.get("browser_utc_offset_minutes")

        self.conn.execute(
            """INSERT INTO runs (run_id, keyword, scope, group_url, match_mode, status, started_at, finished_at,
                                 run_dir, tool_version, batch_id, browser_utc_offset_minutes, records, stats,
                                 warnings, imported_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, finished_at=excluded.finished_at,
                 run_dir=excluded.run_dir, records=excluded.records, stats=excluded.stats,
                 warnings=excluded.warnings, imported_at=excluded.imported_at""",
            (run_id, run.get("keyword"), run.get("scope"), run.get("group_url"), run.get("match_mode"),
             run.get("status"), run.get("started_at"), run.get("finished_at"), rel_dir, run.get("version"),
             run.get("batch_id"), offset, len(results), json.dumps(run.get("stats") or {}),
             json.dumps(run.get("warnings") or [], ensure_ascii=False), utc_now_iso()))

        counts = {"new_items": 0, "merged": 0, "already_imported": 0, "excluded": 0}
        touched: set[str] = set()
        for rec in results:
            if (run_id, _record_id(rec)) in self._exclusions:
                counts["excluded"] += 1
                touched |= self._drop_sighting(run_id, _record_id(rec))
                continue
            item_id, outcome = self._add_record(rec, run_id, run.get("keyword"), rel_dir, offset)
            counts[outcome] += 1
            touched.add(item_id)
        self._refresh_seen(touched)
        self._sync_labels()
        self.conn.commit()
        return {"run_id": run_id, "records": len(results), **counts}

    def import_all(self, output_root: Path | str) -> dict:
        root = Path(output_root)
        runs, errors = 0, []
        totals = {"records": 0, "new_items": 0, "merged": 0, "already_imported": 0, "excluded": 0}
        for path in sorted(root.glob(f"*/*/{RESULTS_FILE}")):
            try:
                r = self.import_run(path.parent, root)
            except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
                errors.append(f"{path.parent}: {exc}")
                continue
            runs += 1
            for k in totals:
                totals[k] += r[k]
        return {"runs": runs, **totals, "errors": errors}

    def _add_record(self, rec: dict, run_id: str, run_keyword: str | None, rel_dir: str,
                    offset: int | None) -> tuple[str, str]:
        rec = _clean_record(rec)
        times = resolve(rec.get("time_exact"), rec.get("time_text"), rec.get("captured_at"), offset)
        shot = f"{rel_dir}/{rec['screenshot_path']}" if rec.get("screenshot_path") else None
        uk, ck = url_key(rec), content_key(rec)
        record_id = _record_id(rec)

        prior = self.conn.execute("SELECT item_id FROM sightings WHERE run_id = ? AND record_id = ?",
                                  (run_id, record_id)).fetchone()
        existing = None
        if prior:
            existing = self.conn.execute("SELECT * FROM items WHERE item_id = ?", (prior[0],)).fetchone()
        if existing is None and uk:
            existing = self.conn.execute("SELECT * FROM items WHERE url_key = ?", (uk,)).fetchone()
        if existing is None and ck:
            for row in self.conn.execute("SELECT * FROM items WHERE content_key = ?", (ck,)):
                # Same text with two different stable URLs, or by two different authors, = two posts.
                if _same_url_identity(row["url_key"], uk) and _same_author(row["author_name"], rec.get("author_name")):
                    existing = row
                    break

        if existing is None:
            item_id = self._new_item_id(uk or ck or f"{run_id}|{record_id}")
            text = rec.get("text") or ""
            values = {
                "item_id": item_id, "url_key": uk, "content_key": ck, "kind": rec.get("kind") or "unknown",
                **{f: rec.get(f) for f in FILL_FIELDS}, "text": text,
                **times, "language": detect_language(text), "screenshot_path": shot,
            }
            cols = ", ".join(values)
            self.conn.execute(f"INSERT INTO items ({cols}) VALUES ({', '.join('?' * len(values))})",
                              tuple(values.values()))
            outcome = "new_items"
        else:
            item_id = existing["item_id"]
            upd = self._merge(existing, rec, uk, ck, times, shot)
            if upd:
                sets = ", ".join(f"{k} = ?" for k in upd)
                self.conn.execute(f"UPDATE items SET {sets} WHERE item_id = ?", (*upd.values(), item_id))
            outcome = "already_imported" if prior else "merged"

        self.conn.execute(
            """INSERT OR IGNORE INTO sightings (item_id, run_id, record_id, keyword, source, search_rank, kind,
                 keyword_verified, matched_in, matched_terms, match_snippet, screenshot_path, captured_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item_id, run_id, record_id, rec.get("keyword") or run_keyword, rec.get("source"),
             rec.get("search_rank"), rec.get("kind"),
             None if rec.get("keyword_verified") is None else int(bool(rec.get("keyword_verified"))),
             rec.get("matched_in"), json.dumps(rec.get("matched_terms") or [], ensure_ascii=False),
             rec.get("match_snippet"), shot, rec.get("captured_at")))
        return item_id, outcome

    def _new_item_id(self, seed: str) -> str:
        n = 0
        while True:
            salted = seed if n == 0 else f"{seed}#{n}"
            item_id = "i_" + hashlib.sha1(salted.encode("utf-8")).hexdigest()[:12]
            if not self.conn.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,)).fetchone():
                return item_id
            n += 1

    @staticmethod
    def _merge(old: sqlite3.Row, rec: dict, uk: str | None, ck: str | None, times: dict, shot: str | None) -> dict:
        upd: dict = {}
        if uk and (not old["url_key"] or (_weak(old["url_key"]) and not _weak(uk))):
            upd["url_key"] = uk
            for f in ("post_url", "comment_url"):  # the stable URL replaces a session-specific one
                if rec.get(f):
                    upd[f] = rec[f]
        if ck and not old["content_key"]:
            upd["content_key"] = ck
        if _kind_rank(rec.get("kind")) < _kind_rank(old["kind"]):
            upd["kind"] = rec["kind"]
        for f in FILL_FIELDS:
            if not old[f] and rec.get(f) and f not in upd:
                upd[f] = rec[f]
        old_text = clean_text(old["text"] or "")
        text = rec.get("text") or ""
        best = text if len(text) > len(old_text) else old_text  # e.g. this time "See more" was expanded
        if best != old["text"]:
            upd["text"] = best
        language = detect_language(upd.get("text", old["text"]))  # also refreshes after rule changes
        if language != old["language"]:
            upd["language"] = language
        if finer(times["posted_at_precision"], old["posted_at_precision"]):
            upd.update(times)
        if shot and not old["screenshot_path"]:
            upd["screenshot_path"] = shot
        return upd

    # ---- exclusions -----------------------------------------------------------

    def _load_exclusions(self) -> dict[tuple[str, str], dict]:
        try:
            entries = json.loads(self.exclusions_path.read_text(encoding="utf-8")).get("exclusions", [])
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError(f"Can't read {self.exclusions_path}: {exc}") from exc
        return {(e["run_id"], e["record_id"]): e for e in entries if e.get("run_id") and e.get("record_id")}

    def _save_exclusions(self) -> None:
        data = {"about": "Records left out of the dataset. Delete an entry and run `fbscout db import` to restore it.",
                "exclusions": list(self._exclusions.values())}
        self.exclusions_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _drop_sighting(self, run_id: str, record_id: str) -> set[str]:
        """Remove one sighting; an item left without sightings is removed too. Returns the remaining item ids."""
        rows = self.conn.execute("SELECT item_id FROM sightings WHERE run_id = ? AND record_id = ?",
                                 (run_id, record_id)).fetchall()
        kept = set()
        for (item_id,) in rows:
            self.conn.execute("DELETE FROM sightings WHERE item_id = ? AND run_id = ? AND record_id = ?",
                              (item_id, run_id, record_id))
            if self.conn.execute("SELECT 1 FROM sightings WHERE item_id = ?", (item_id,)).fetchone():
                kept.add(item_id)
            else:
                self.conn.execute("DELETE FROM items WHERE item_id = ?", (item_id,))
                self.conn.execute("DELETE FROM annotations WHERE item_id = ?", (item_id,))
        return kept

    def exclude_items(self, item_ids: list[str], reason: str) -> dict:
        """Leave items out of the dataset, now and in every later import. The run folders stay as they are."""
        if not reason or not reason.strip():
            raise ValueError("Give a reason for the exclusion (it is kept for the record).")
        excluded, missing, records = [], [], 0
        for item_id in dict.fromkeys(item_ids):
            rows = self.conn.execute("SELECT run_id, record_id, keyword FROM sightings WHERE item_id = ?",
                                     (item_id,)).fetchall()
            if not rows:
                missing.append(item_id)
                continue
            for r in rows:
                self._exclusions[(r["run_id"], r["record_id"])] = {
                    "run_id": r["run_id"], "record_id": r["record_id"], "item_id": item_id,
                    "keyword": r["keyword"], "reason": reason.strip(), "excluded_at": utc_now_iso()}
                self._drop_sighting(r["run_id"], r["record_id"])
                records += 1
            excluded.append(item_id)
        self._save_exclusions()
        self.conn.commit()
        return {"excluded_items": len(excluded), "excluded_records": records, "not_found": missing,
                "exclusions_file": str(self.exclusions_path.resolve()), "items_left": self.count()}

    # ---- annotations (Phase 3) ------------------------------------------------
    # One per item, keyword and method (+ annotator for people); fields in analysis.py.
    # Per keyword, because a post can be negative about one brand and positive about
    # another. Mirrored in labels.json so they survive a rebuild from the run folders.

    def _load_labels_file(self) -> dict[tuple, dict]:
        try:
            entries = json.loads(self.labels_path.read_text(encoding="utf-8")).get("labels", [])
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, AttributeError) as exc:
            raise ValueError(f"Can't read {self.labels_path}: {exc}") from exc
        out = {}
        for e in entries:
            if not (e.get("item_id") and e.get("keyword") and e.get("sentiment")):
                continue
            if "method" not in e:   # v0.3 file: sentiment only, "labeler" instead of method
                human = e.get("labeler") == "human"
                e = {**e, "method": "human" if human else "agent", "annotator": "gold" if human else "",
                     "model": None if human else e.get("labeler")}
            out[(e["item_id"], e["keyword"].lower(), e["method"], e.get("annotator") or "")] = e
        return out

    def _save_labels_file(self) -> None:
        data = {"about": "Annotations (sentiment, aspects, churn, feedback type) per item, keyword and method. "
                         "Restored into the dataset on import. See docs/ANALYSIS.md.",
                "labels": list(self._labels_file.values())}
        self.labels_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _migrate_labels_table(self) -> None:
        """v0.3 kept sentiment-only labels in a `labels` table: move them into `annotations`."""
        if not self.conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'labels'").fetchone():
            return
        for r in self.conn.execute("SELECT item_id, keyword, sentiment, reason, labeler, labeled_at FROM labels"):
            human = r["labeler"] == "human"
            self.conn.execute(
                "INSERT OR IGNORE INTO annotations (item_id, keyword, method, annotator, sentiment, reason, model, "
                "rubric_version, labeled_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r["item_id"], r["keyword"], "human" if human else "agent", "gold" if human else "", r["sentiment"],
                 r["reason"], None if human else r["labeler"], "1", r["labeled_at"]))
        self.conn.execute("DROP TABLE labels")

    def _insert_annotation(self, item_id: str, keyword: str, method: str, annotator: str, ann: dict,
                           model: str | None, rubric_version: str | None, labeled_at: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO annotations (item_id, keyword, method, annotator, sentiment, aspects, churn, "
            "churn_target, feedback_type, reason, model, rubric_version, labeled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, keyword, method, annotator, ann["sentiment"],
             None if ann.get("aspects") is None else json.dumps(ann["aspects"], ensure_ascii=False),
             ann.get("churn"), ann.get("churn_target"), ann.get("feedback_type"), ann.get("reason"), model,
             rubric_version, labeled_at))

    def _sync_labels(self) -> None:
        """Put annotations from labels.json back into the table (after a rebuild), following the run records."""
        present = {(r[0], r[1].lower(), r[2], r[3]) for r in
                   self.conn.execute("SELECT item_id, keyword, method, annotator FROM annotations")}
        for key, e in self._labels_file.items():
            if key in present:
                continue
            item_id = e["item_id"]
            if not self.conn.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,)).fetchone():
                item_id = next((row[0] for run_id, record_id in e.get("refs") or []
                                for row in self.conn.execute("SELECT item_id FROM sightings WHERE run_id = ? "
                                                             "AND record_id = ?", (run_id, record_id))), None)
            if item_id:
                self._insert_annotation(item_id, e["keyword"], e["method"], e.get("annotator") or "", e,
                                        e.get("model"), e.get("rubric_version"), e.get("labeled_at"))

    def item_keywords(self, item_id: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT keyword FROM sightings WHERE item_id = ?", (item_id,))]

    def label_queue(self, *, method: str = "agent", annotator: str = "", keyword: str | None = None,
                    run_id: str | None = None, batch_id: str | None = None, limit: int | None = 20,
                    text_chars: int | None = 1500) -> dict:
        """(item, keyword) pairs that this method (and annotator) hasn't labeled yet, with their text."""
        clauses, params = ["a.item_id IS NULL"], [method, annotator]
        if keyword:
            clauses.append("s.keyword = ? COLLATE NOCASE")
            params.append(keyword)
        if run_id:
            clauses.append("s.run_id = ?")
            params.append(run_id)
        if batch_id:
            clauses.append("r.batch_id = ?")
            params.append(batch_id)
        base = (" FROM sightings s JOIN runs r ON r.run_id = s.run_id "
                "LEFT JOIN annotations a ON a.item_id = s.item_id AND a.keyword = s.keyword COLLATE NOCASE "
                "AND a.method = ? AND a.annotator = ? WHERE " + " AND ".join(clauses))
        pairs = self.conn.execute("SELECT DISTINCT s.item_id, s.keyword" + base + " ORDER BY s.item_id, s.keyword",
                                  params).fetchall()
        out = []
        for item_id, kw in (pairs if limit is None else pairs[: max(1, int(limit))]):
            row = self.conn.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
            text = row["text"] or ""
            if text_chars and len(text) > text_chars:
                text = text[:text_chars] + "…"
            out.append({
                "item_id": item_id, "keyword": kw, "kind": row["kind"], "language": row["language"], "text": text,
                "image_text": row["image_text"], "author_name": row["author_name"], "group_name": row["group_name"],
                "price": row["price"], "url": row["comment_url"] or row["post_url"], "posted_date": row["posted_date"],
                "screenshot_path": row["screenshot_path"],
            })
        return {"to_label": out, "remaining": len(pairs)}

    def annotate(self, entries: list[dict], method: str = "agent", annotator: str = "",
                 model: str | None = None, rubric_version: str = RUBRIC_VERSION) -> dict:
        """Save annotations: [{item_id, sentiment, reason, aspects?, churn?, churn_target?, feedback_type?,
        keyword?}]. The keyword may be left out when the item was found by only one keyword."""
        if method not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}")
        if method == "human" and not annotator:
            raise ValueError("human annotations need an annotator name (e.g. A, B or gold)")
        done, errors = 0, []
        for entry in entries:
            item_id = (entry.get("item_id") or "").strip()
            found = self.item_keywords(item_id)
            if not found:
                errors.append(f"{item_id or '?'}: no such item")
                continue
            keyword = (entry.get("keyword") or "").strip()
            if not keyword:
                if len(found) > 1:
                    errors.append(f"{item_id}: found by several keywords ({', '.join(found)}); say which one")
                    continue
                keyword = found[0]
            keyword = next((k for k in found if k.lower() == keyword.lower()), keyword)
            try:
                ann = normalize_annotation(entry)
            except ValueError as exc:
                errors.append(f"{item_id}: {exc}")
                continue
            now = utc_now_iso()
            used_model = entry.get("model") or model
            self._insert_annotation(item_id, keyword, method, annotator, ann, used_model, rubric_version, now)
            refs = [list(r) for r in self.conn.execute("SELECT run_id, record_id FROM sightings WHERE item_id = ?",
                                                       (item_id,))]
            self._labels_file[(item_id, keyword.lower(), method, annotator)] = {
                "item_id": item_id, "keyword": keyword, "method": method, "annotator": annotator, **ann,
                "model": used_model, "rubric_version": rubric_version, "labeled_at": now, "refs": refs}
            done += 1
        if done:
            self._save_labels_file()
        self.conn.commit()
        return {"labeled": done, "errors": errors}

    def label_items(self, labels: list[dict], labeler: str = "agent") -> dict:
        """v0.3 entry point: labels by an agent, or by a person ("human") as the gold label."""
        if labeler == "human":
            return self.annotate(labels, method="human", annotator="gold")
        return self.annotate(labels, method="agent", model=labeler if labeler != "agent" else None)

    def annotations(self, *, method: str | None = None, annotator: str | None = None,
                    keyword: str | None = None) -> list[dict]:
        clauses, params = [], []
        for column, value in (("method", method), ("annotator", annotator)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if keyword:
            clauses.append("keyword = ? COLLATE NOCASE")
            params.append(keyword)
        sql = "SELECT * FROM annotations" + (" WHERE " + " AND ".join(clauses) if clauses else "")
        return [_annotation_row(r) for r in self.conn.execute(sql + " ORDER BY item_id, keyword", params)]

    def _annotations_for(self, item_ids: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for chunk in (item_ids[i:i + 500] for i in range(0, len(item_ids), 500)):
            q = f"SELECT * FROM annotations WHERE item_id IN ({', '.join('?' * len(chunk))}) ORDER BY keyword, method"
            for r in self.conn.execute(q, chunk):
                out.setdefault(r["item_id"], []).append(_annotation_row(r))
        return out

    def _refresh_seen(self, item_ids: set[str]) -> None:
        for item_id in item_ids:
            self.conn.execute(
                """UPDATE items SET
                     first_seen = (SELECT MIN(captured_at) FROM sightings WHERE item_id = :id),
                     last_seen = (SELECT MAX(captured_at) FROM sightings WHERE item_id = :id),
                     times_seen = (SELECT COUNT(DISTINCT run_id) FROM sightings WHERE item_id = :id),
                     first_run_id = (SELECT run_id FROM sightings WHERE item_id = :id ORDER BY captured_at LIMIT 1),
                     last_run_id = (SELECT run_id FROM sightings WHERE item_id = :id ORDER BY captured_at DESC LIMIT 1)
                   WHERE item_id = :id""", {"id": item_id})

    # ---- queries ------------------------------------------------------------

    @staticmethod
    def _where(keyword: str | None = None, kind: str | list[str] | None = None,
               language: str | list[str] | None = None, group: str | None = None, since: str | None = None,
               until: str | None = None, contains: str | None = None, sentiment: str | list[str] | None = None,
               run_id: str | None = None, batch_id: str | None = None, aspect: str | list[str] | None = None,
               churn: str | list[str] | None = None, feedback_type: str | list[str] | None = None,
               method: str | None = None) -> tuple[str, list]:
        """Item filters. sentiment / aspect / churn / feedback_type use the primary label, or the labels of
        `method` ("claude-api", "agent", "model", "human:<annotator>") when given."""
        clauses, params = [], []
        if run_id:
            clauses.append("item_id IN (SELECT item_id FROM sightings WHERE run_id = ?)")
            params.append(run_id)
        if batch_id:
            clauses.append("item_id IN (SELECT s.item_id FROM sightings s JOIN runs r ON r.run_id = s.run_id "
                           "WHERE r.batch_id = ?)")
            params.append(batch_id)
        label_conds, label_params = [], []
        for column, value in (("sentiment", sentiment), ("churn", churn), ("feedback_type", feedback_type)):
            values = [v.lower() for v in _split_list(value)]
            if values:
                label_conds.append(f"p.{column} IN ({', '.join('?' * len(values))})")
                label_params.extend(values)
        aspects = [v.lower() for v in _split_list(aspect)]
        if aspects:
            label_conds.append("EXISTS (SELECT 1 FROM json_each(p.aspects) j WHERE json_extract(j.value, '$.aspect') "
                               f"IN ({', '.join('?' * len(aspects))}))")
            label_params.extend(aspects)
        if label_conds:
            source, source_params = _label_source(method)
            if keyword:
                label_conds.append("p.keyword = ? COLLATE NOCASE")
                label_params.append(keyword)
            clauses.append(f"item_id IN (SELECT p.item_id FROM {source} AS p WHERE {' AND '.join(label_conds)})")
            params.extend(source_params + label_params)
        if keyword:
            clauses.append("item_id IN (SELECT item_id FROM sightings WHERE keyword = ? COLLATE NOCASE)")
            params.append(keyword)
        for column, value in (("kind", kind), ("language", language)):
            values = _split_list(value)
            if values:
                clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
                params.extend(values)
        if group:
            clauses.append("(group_name LIKE ? OR group_url LIKE ?)")
            params.extend([f"%{group}%"] * 2)
        if since:
            clauses.append("posted_date >= ?")
            params.append(since)
        if until:
            clauses.append("posted_date <= ?")
            params.append(until)
        if contains:
            clauses.append("(text LIKE ? OR image_text LIKE ? OR price LIKE ? OR location LIKE ?)")
            params.extend([f"%{contains}%"] * 4)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _count_by(self, column: str, where: str, params: list, limit: int | None = None) -> dict:
        sql = (f"SELECT COALESCE({column}, 'unknown') AS k, COUNT(*) AS n FROM items {where} "
               f"GROUP BY k ORDER BY n DESC, k" + (f" LIMIT {int(limit)}" if limit else ""))
        return {row["k"]: row["n"] for row in self.conn.execute(sql, params)}

    def stats(self, keyword: str | None = None) -> dict:
        where, params = self._where(keyword=keyword)
        total = self.conn.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()[0]
        seen = self.conn.execute(f"SELECT MIN(first_seen), MAX(last_seen), SUM(times_seen > 1), "
                                 f"SUM(posted_date IS NULL) FROM items {where}", params).fetchone()
        kw_sql = "SELECT keyword, COUNT(DISTINCT item_id) AS n FROM sightings"
        kw_params: list = []
        if keyword:
            kw_sql += " WHERE keyword = ? COLLATE NOCASE"
            kw_params.append(keyword)
        by_keyword = {r["keyword"]: r["n"] for r in self.conn.execute(kw_sql + " GROUP BY keyword ORDER BY n DESC",
                                                                      kw_params)}
        run_sql = "SELECT COUNT(*) FROM runs" + (" WHERE keyword = ? COLLATE NOCASE" if keyword else "")
        runs = self.conn.execute(run_sql, [keyword] if keyword else []).fetchone()[0]
        month = "substr(posted_date, 1, 7)"
        analysis = self.label_stats(keyword=keyword)
        return {
            "dataset": str(self.path.resolve()),
            "runs": runs,
            "items": total,
            "excluded_records": len(self._exclusions),
            "seen_in_more_than_one_run": seen[2] or 0,
            "items_without_date": seen[3] or 0,
            "first_seen": seen[0],
            "last_seen": seen[1],
            "by_keyword": by_keyword,
            "by_kind": self._count_by("kind", where, params),
            "by_language": self._count_by("language", where, params),
            "by_sentiment": analysis["by_sentiment"],
            "not_labeled": total - self.count(keyword=keyword, sentiment=list(SENTIMENTS)),
            "labels_by_method": analysis["labels_by_method"],
            "by_month_posted": dict(sorted(self._count_by(month, where, params).items())),
            "top_groups": self._count_by("group_name", (where + " AND " if where else "WHERE ")
                                         + "group_name IS NOT NULL", params, limit=10),
        }

    def label_stats(self, keyword: str | None = None, method: str | None = None, **filters) -> dict:
        """Phase 3 overview from the primary labels (or one method's): sentiment, aspects, churn,
        feedback types, and sentiment per month posted."""
        where, params = self._where(keyword=keyword, **filters)
        source, source_params = _label_source(method)
        joins = f" FROM {source} AS p JOIN items i ON i.item_id = p.item_id"
        cond = f" WHERE p.item_id IN (SELECT item_id FROM items {where})"
        bp = source_params + list(params)
        if keyword:
            cond += " AND p.keyword = ? COLLATE NOCASE"
            bp.append(keyword)
        base = joins + cond

        def counts(column: str) -> dict:
            q = f"SELECT p.{column} AS k, COUNT(*) AS n{base} AND p.{column} IS NOT NULL GROUP BY k ORDER BY n DESC"
            return {r["k"]: r["n"] for r in self.conn.execute(q, bp)}

        by_aspect: dict[str, dict] = {}
        q = (f"SELECT json_extract(j.value, '$.aspect') AS a, json_extract(j.value, '$.sentiment') AS s, COUNT(*) AS n"
             f"{joins}, json_each(p.aspects) j{cond} GROUP BY a, s")
        for r in self.conn.execute(q, bp):
            by_aspect.setdefault(r["a"], {})[r["s"]] = r["n"]
        by_aspect = dict(sorted(by_aspect.items(), key=lambda kv: -sum(kv[1].values())))
        monthly: dict[str, dict] = {}
        q = f"SELECT substr(i.posted_date, 1, 7) AS m, p.sentiment AS s, COUNT(*) AS n{base} GROUP BY m, s"
        for r in self.conn.execute(q, bp):
            monthly.setdefault(r["m"] or "unknown", {})[r["s"]] = r["n"]
        targets = {r["k"]: r["n"] for r in self.conn.execute(
            f"SELECT p.churn_target AS k, COUNT(*) AS n{base} AND p.churn IN ('considering', 'switched') "
            f"AND p.churn_target IS NOT NULL GROUP BY k ORDER BY n DESC LIMIT 10", bp)}
        by_method = {}
        for r in self.conn.execute("SELECT method, annotator, COUNT(*) AS n FROM annotations "
                                   f"WHERE item_id IN (SELECT item_id FROM items {where})"
                                   + (" AND keyword = ? COLLATE NOCASE" if keyword else "") + " GROUP BY method, annotator",
                                   list(params) + ([keyword] if keyword else [])):
            by_method[r["method"] + (f":{r['annotator']}" if r["annotator"] else "")] = r["n"]
        return {
            "labels_from": method or "primary (gold > claude-api > agent > model)",
            "labeled": self.conn.execute(f"SELECT COUNT(*){base}", bp).fetchone()[0],
            "by_sentiment": counts("sentiment"),
            "by_feedback_type": counts("feedback_type"),
            "by_churn": counts("churn"),
            "churn_targets": targets,
            "by_aspect": by_aspect,
            "sentiment_by_month": dict(sorted(monthly.items())),
            "labels_by_method": by_method,
        }

    def items(self, *, limit: int | None = 50, offset: int = 0, text_chars: int | None = 400,
              **filters) -> list[dict]:
        """Items, newest post first. `text_chars=None` returns full texts."""
        where, params = self._where(**filters)
        sql = (f"SELECT * FROM items {where} ORDER BY posted_at IS NULL, posted_at DESC, last_seen DESC"
               + (f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit else ""))
        rows = [dict(r) for r in self.conn.execute(sql, params)]
        keywords: dict[str, list[str]] = {}
        ids = [r["item_id"] for r in rows]
        for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
            q = f"SELECT DISTINCT item_id, keyword FROM sightings WHERE item_id IN ({', '.join('?' * len(chunk))})"
            for r in self.conn.execute(q, chunk):
                keywords.setdefault(r["item_id"], []).append(r["keyword"])
        annotations = self._annotations_for(ids)
        want = (filters.get("keyword") or "").lower()
        method = filters.get("method")
        for r in rows:
            r["keywords"] = sorted(k for k in keywords.get(r["item_id"], []) if k)
            r["annotations"] = annotations.get(r["item_id"], [])
            chosen = _primary_per_keyword(r["annotations"], method)
            if want:
                chosen = [a for a in chosen if a["keyword"].lower() == want]
            r.update(_label_fields(chosen))
            if text_chars and r.get("text") and len(r["text"]) > text_chars:
                r["text"] = r["text"][:text_chars] + "…"
        return rows

    def count(self, **filters) -> int:
        where, params = self._where(**filters)
        return self.conn.execute(f"SELECT COUNT(*) FROM items {where}", params).fetchone()[0]
