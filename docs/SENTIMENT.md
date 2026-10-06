# FB Scout — Sentiment labels

FB Scout can return **only negative posts** about a keyword. The tool collects as usual. Then
an AI agent (Claude in Claude Code) reads every saved post, comment or listing and labels it
**negative**, **neutral** or **positive** towards the keyword, with a one-line reason. Reports and
exports can then be filtered to the negatives. Nothing is thrown away: all results stay in the
dataset with their labels.

This is the "LLM classification with a fixed rubric" approach planned for Phase 3
([PLAN.md](PLAN.md#phase-3-sentiment-and-brand-analysis)). It needs no model download and works
in English, Urdu and Roman Urdu. It costs AI tokens, and it only runs when the tool is driven
by an agent (the plain CLI can't label, except by hand).

---

## 1. Rubric

Sentiment is judged **towards the keyword** (brand, product or topic), not the post's general mood.

| Label | When |
|---|---|
| **negative** | complaint, criticism, bad experience, defect or failure report, scam/fraud claim, refund or service problem, warning others, anger, switching away ("never again", "switching to Y"), sarcastic praise |
| **positive** | praise, recommendation, satisfaction |
| **neutral** | ads, sale listings, price lists, announcements, questions without a complaint, news, an opinion about something else |

- Roman Urdu / Urdu cues: *bekar, ghatiya, kharab, fraud, dhoka, paisay zaya, masla*, شکایت, خراب.
- An edit counts: "Appreciation post … Edit: worst experience ever" is negative.
- A post about two brands gets one label per keyword ("Y failed twice, X has been perfect" is
  negative for Y and positive for X).
- The reason quotes the cue, e.g. `"VERY DISAPPOINTED WITH YOUR WARRANTY SERVICE", visited the
  service centre 4 times`.

The rubric is part of the `fb_label_items` tool description, so every agent that uses the MCP
server gets the same instructions.

---

## 2. Workflow

```
fb_search(keyword, only_negative=true)       → run_id + next_step
  └ repeat:  fb_label_queue(run_id)          → up to 20 unlabelled items with their text
             fb_label_items([{item_id, sentiment, reason}, ...])
  └ fb_dataset_items(run_id, sentiment="negative")   → the report
```

For a study: `fb_label_queue(batch_id=…)`. For everything collected: `fb_label_queue(keyword=…)`.

**Finding negatives.** A bare brand name mostly returns the brand's own ads, and complaints
under those ads rarely repeat the brand name, so the keyword check doesn't keep them. In the live
test on 2026-10-06, `Inverex` (global search) gave 19 results: brand ads and brand replies,
0 negative. `Inverex problem` with `match_mode: all` gave 10 results: 5 negative (warranty and
service complaints, an electric-shock report, a battery-drain problem) and 5 neutral (dealer ads,
sale posts, setup questions). The `fb-search` skill therefore suggests brand + complaint word,
customer groups, and a larger `max_results` for negative-only requests.

---

## 3. Storage

- `labels` table in the dataset: `item_id`, `keyword`, `sentiment`, `reason`, `labeler`
  (`claude`, `agent` or `human`), `labeled_at`.
- `labels.json` next to the dataset keeps the same labels with the run records they belong to,
  so they are restored when the dataset is rebuilt from the run folders.
- `fbscout db stats` / `fb_dataset_stats`: `by_sentiment` and `not_labeled`.
- Filters: `sentiment` in `fb_dataset_items`, `fb_export` and `fbscout db export --sentiment negative`.
- Export columns: `sentiment`, `sentiment_reason` (for several keywords:
  `Brand X: positive | Brand Y: negative`).

---

## 4. Checking the labels (for the thesis)

AI labels need a quality check before they're used as findings:

1. Draw a random sample (e.g. 200–300 items) with `fbscout db export --format csv`.
2. Two people label it independently with the rubric above. Record their labels with
   `fbscout db label ITEM_ID --sentiment negative --reason "..."`, which is stored with
   `labeler: human`. Measure their agreement with Cohen's kappa.
3. Compare the agreed human labels with the AI labels. Report precision, recall and F1 for
   `negative`.

Labels by hand overwrite the AI label for that item and keyword. Keep the AI labels of the
sample in a separate export first if you want to compare them.
