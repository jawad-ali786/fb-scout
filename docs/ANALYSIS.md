# FB Scout — Analysis (Phase 3)

Phase 3 turns the collected posts into research data: **what people say about a brand**,
**which aspects they complain about**, **whether they are leaving it**, and **how well each
labeling method does** compared with people.

| Research question (PLAN.md §1) | Where the answer comes from |
|---|---|
| 1. Positive / negative / neutral? | `sentiment` per item and keyword |
| 2. Which product aspects get complaints? | `aspects` (price, quality, service, …) each with its own sentiment |
| 3. Churn signals? | `churn` (none / considering / switched) and `churn_target` |
| 4. Change over time? | `sentiment_by_month` in the statistics (posted date) |

---

## 1. Labels

One **annotation** per item (post, comment or listing), **keyword** and **method**. Everything
is judged *towards the keyword*: a post can be negative about Brand Y and positive about Brand X.

| Field | Values |
|---|---|
| `sentiment` | `negative`, `neutral`, `positive` |
| `aspects` | list of `{aspect, sentiment}`; aspects: `price`, `product_quality`, `performance`, `durability`, `installation`, `delivery`, `customer_service`, `warranty`, `availability`, `safety`, `other`. Empty when the item has no opinion (e.g. an ad) |
| `churn` | `none`, `considering` (thinking of leaving, asking for alternatives), `switched` (left, won't buy again) |
| `churn_target` | the brand they move to, if named |
| `feedback_type` | `complaint`, `defect_report`, `feature_request`, `praise`, `question`, `advertisement`, `news`, `other` |
| `reason` | one sentence quoting the deciding words |

The full **rubric** (definitions, Roman Urdu / Urdu cues, edge cases such as edited posts) is in
`src/fbscout/analysis.py` (`RUBRIC`, version `2`). It's the same text for every method: the
Claude prompt, the agent tool description, and the instructions given to human annotators.

---

## 2. Methods

| Method | Who labels | Fields | Cost | Reproducible |
|---|---|---|---|---|
| `human` | people, via labeling sheets. Annotator `A`, `B`, … and `gold` for final labels | all | time | — |
| `claude-api` | a fixed Claude model (`claude-opus-5-5`, effort `medium`), fixed rubric, JSON schema | all | money (see §5) | model, effort, rubric version and the model that answered are stored |
| `agent` | Claude in Claude Code (or another agent) through the MCP tools | all | your Claude Code use | session-dependent |
| `model` | local XLM-RoBERTa sentiment model (`cardiffnlp/twitter-xlm-roberta-base-sentiment`) | sentiment only | free, offline | yes (same model, same input) |

All methods' labels are kept **side by side**, so they can be compared. Reports, filters and
exports use one **primary label** per item and keyword, in this order: `human` with annotator
`gold`, then `claude-api`, then `agent`, then `model`. Annotators `A`/`B` never decide the
primary label; they exist for the agreement check. To use one method's labels instead, pass
`label_method` / `--label-method` (e.g. `model`, `human:A`).

**Storage:** the `annotations` table in the dataset, mirrored with the run records in
`labels.json` so labels survive a rebuild. Labels from v0.3 (sentiment only) were moved over
automatically as method `agent`.

---

## 3. Workflows

### Negative posts only (in Claude Code)
`/fb-scout:fb-search "Brand X problem" match=all negative`: Claude collects, labels each result
with all fields (`fb_label_queue` → `fb_label_items`), and reports only the negatives. See the
`fb-search` skill.

### Analyse what has been collected
`/fb-scout:fb-analyze Brand X`: labels what isn't labeled yet and summarises sentiment,
complaint aspects, churn signals and the trend per month (`fb_dataset_stats` → `analysis`).

### Claude API labeling (reproducible, scriptable)
```
uv sync --extra claude                       # once, in plugins/fb-scout/server
set ANTHROPIC_API_KEY=sk-ant-...             # Windows (macOS/Linux: export ...)
uv run fbscout analyze --method claude-api --keyword "Brand X" --dry-run    # count + cost estimate
uv run fbscout analyze --method claude-api --keyword "Brand X" --limit 200
uv run fbscout analyze --method claude-api --mode batch --limit 500         # half price, later
uv run fbscout analyze --collect msgbatch_...  --wait 30                     # save the batch's labels
```
Each run reports the labels saved, errors (e.g. a declined item, kept for a retry), the
model that answered (in sync mode a request declined by a safety classifier is retried
server-side on Anthropic's recommended fallback model, and that model is recorded), token
usage and the **actual cost**.

### Local model
```
uv sync --extra ml                           # once: PyTorch (CPU) + transformers
uv run --extra ml fbscout analyze --method model --limit 1000   # the model downloads on first use (~1.1 GB)
```

### Gold set and evaluation
```
uv run fbscout gold sample --n 300 [--stratify]       # _gold/gold_sample_<time>.csv + _instructions.md
#   two people fill the sheet independently in Excel
uv run fbscout gold import gold_A.csv --annotator A
uv run fbscout gold import gold_B.csv --annotator B
uv run fbscout gold adjudicate --a A --b B            # the items they disagree on
#   a third person fills the final columns
uv run fbscout gold import adjudicated.csv --annotator gold
uv run fbscout evaluate                               # _reports/evaluation_<time>.md + .json
```
- The sheet is **blind**: no AI labels in it.
- `--stratify` draws equal numbers per (primary) sentiment, so negatives aren't swamped by ads.
  Report this in the thesis, because it changes the class balance.
- The reference is the `gold` labels, plus the items where A and B agree (per field).
- `evaluate` reports per method:
  - for sentiment, churn and feedback type: accuracy, precision, recall and F1 per class,
    macro-F1, the confusion matrix and Cohen's kappa;
  - for aspects: precision, recall and F1 per aspect, plus micro-F1;
  - Cohen's kappa between annotators A and B.
- `fbscout evaluate --reference agent --methods model` compares two methods without a gold set.
  It measures agreement between them, not accuracy.

---

## 4. Filters, statistics and exports

- Filters everywhere (items, exports, statistics): `sentiment`, `aspect`, `churn`,
  `feedback_type`, `label_method`. Examples:
  - `fbscout db export --aspect customer_service --sentiment negative`
  - `fb_dataset_items(churn="considering,switched")`
- `fbscout db stats` / `fb_dataset_stats` → `analysis`. It contains `by_sentiment`,
  `by_aspect` (with sentiment per aspect), `by_churn`, `churn_targets`, `by_feedback_type`,
  `sentiment_by_month` and `labels_by_method`.
- Export columns: `sentiment`, `sentiment_reason`, `aspects` (`price:negative; warranty:negative`),
  `churn`, `churn_target`, `feedback_type`, `label_method`.

---

## 5. Cost of the Claude API method

Claude Opus 5.5 costs $4 per million input tokens and $20 per million output tokens. The rubric
is cached, so cache reads cost $0.20 per million. The dominant cost is the model's output
(thinking plus the JSON), which depends on `effort`:

| effort | rough cost per 100 items |
|---|---|
| `low` | ≈ $0.5 |
| `medium` (default) | ≈ $1 |
| `high` | ≈ $2 |

The Batch API halves these. `--dry-run` gives an estimate for the actual items; every run
reports the real cost from the API's token counts. Try `--effort low` on the gold set: if it
scores as well as `medium`, it's half the price.

---

## 6. For the methods section (reproducibility and limits)

- **What to report for the Claude API method:**
  - the model id, the effort and the rubric version;
  - the date of the run;
  - how many items were declined or fell back to another model (`served_by` in the run output).
- **Repeat runs can differ.** Claude Opus 5.5 doesn't take a temperature setting, so two runs
  over the same items can differ slightly. To measure self-consistency, label a sample twice
  (e.g. once in sync and once in batch mode) and compare with `evaluate --reference`.
- **The local model judges overall tone, not the keyword.** "Y failed twice, X is perfect"
  gets one label for both brands. Its training data (tweets) doesn't include Roman Urdu, so
  measure it on the gold set rather than assuming it works.
- **Sampling bias.** Facebook search is personalised and favours recent posts, and a bare brand
  name mostly returns the brand's own advertising. State the search terms used, e.g.
  "Inverex problem": 5 of 10 results negative, versus "Inverex": 0 of 19.
- **Ethics.** Labels and screenshots are personal data. Export with `--anonymize` for anything
  shared, and see PLAN.md §8.
