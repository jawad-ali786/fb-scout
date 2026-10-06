---
name: fb-analyze
description: Analyse what FB Scout collected about a brand, product or topic (Phase 3). Labels posts and comments with sentiment, the aspects people talk about (price, quality, service, warranty, ...), churn signals (leaving the brand) and feedback type, then summarises complaints, churn and the trend over time. Also runs the comparison methods (Claude API, local model), builds the hand-labeled gold set and evaluates the methods (precision, recall, F1, Cohen's kappa). Use when the user asks for brand analysis, sentiment analysis, what people complain about, churn, aspects, comparing labeling methods, a gold set, inter-annotator agreement or evaluation.
argument-hint: "[keyword] [label | summary | claude-api | model | gold sample|import|adjudicate | evaluate]"
allowed-tools: mcp__plugin_fb-scout_fb-scout__fb_dataset_stats, mcp__plugin_fb-scout_fb-scout__fb_dataset_items, mcp__plugin_fb-scout_fb-scout__fb_label_queue, mcp__plugin_fb-scout_fb-scout__fb_label_items, mcp__plugin_fb-scout_fb-scout__fb_annotate, mcp__plugin_fb-scout_fb-scout__fb_collect_batch, mcp__plugin_fb-scout_fb-scout__fb_gold, mcp__plugin_fb-scout_fb-scout__fb_evaluate, mcp__plugin_fb-scout_fb-scout__fb_export, Read
---

# Brand analysis (FB Scout, Phase 3)

User request: $ARGUMENTS

Labels: `sentiment` (towards the keyword), `aspects` (each with its own sentiment), `churn` +
`churn_target`, `feedback_type`, `reason`. Methods: `agent` (you, through the tools),
`claude-api` (fixed model and prompt), `model` (local sentiment model), `human` (annotators A, B,
and `gold`). Reports use the primary label: gold > claude-api > agent > model. Details in
docs/ANALYSIS.md.

## Pick the action

**Summary** (default: "what do people say about X", "main complaints", "is anyone leaving X"):
1. `fb_dataset_stats` with the `keyword`. Look at `analysis.labeled` against `items`.
2. If many items aren't labeled yet, label them first (next action), and say how many you're labeling.
3. Report from `analysis`:
   - the sentiment split (with numbers);
   - the top complaint aspects (`by_aspect`, negatives first);
   - churn: how many items are `considering` or `switched`, and `churn_targets`;
   - the feedback-type mix;
   - the trend per month (`sentiment_by_month`), saying how many items each month has.
4. Back each point with 1–3 example posts from `fb_dataset_items` (filters such as
   `aspect: "customer_service", sentiment: "negative"` or `churn: "considering,switched"`): date,
   group, a short quote and the label's reason.
5. Say what the numbers rest on: how many items, how many labeled and by which method, the
   search terms, and that Facebook search is a sample, not a census.

**Label** ("label the posts", "analyse the new posts"):
1. `fb_label_queue` (with `keyword`, `run_id` or `batch_id`) returns up to 20 items with their text.
2. For each item decide all fields following the rubric in the `fb_label_items` description.
   Judge towards the keyword. Ads and sale posts are neutral with no aspects; an edit counts;
   a question is not a complaint. Read the `screenshot_file` only when the text is unclear.
3. `fb_label_items` with all fields; repeat until `remaining` is 0. For more than about 200 items,
   suggest the Claude API method instead (fixed and reproducible), and ask.

**Claude API** ("label with the API", "reproducible labels", "for the thesis"):
1. `fb_annotate(method="claude-api", dry_run=true, ...)`: tell the user the number of items and the
   estimated cost, and ask before running. Mention `mode="batch"` (half price, results within hours)
   and `effort="low"` (cheaper) as options.
2. Run it without `dry_run`. Report labeled, errors, `served_by` and the actual `cost_usd`.
   Batch mode: give the `batch_id` and use `fb_collect_batch` later.
3. If the result says `unavailable`, pass on its message (the `claude` extra or an API key is missing).

**Local model** ("compare with a model", "XLM-R", "offline"): `fb_annotate(method="model")`. It gives
sentiment only. If `unavailable`, tell the user to run `uv sync --extra ml` in
plugins/fb-scout/server (about 1.3 GB, once).

**Gold set** ("gold set", "annotators", "inter-annotator agreement"):
- `fb_gold(action="sample", n=300)`. The sheet is blind; `stratify=true` balances the sentiment
  classes (say this changes the class balance). Tell the user to give the sheet and its
  instructions file to two people, who label independently.
- `fb_gold(action="import", file=..., annotator="A")`, then the same for B.
- `fb_gold(action="adjudicate")` gives the sheet of disagreements. A third person decides, and the
  sheet is imported with annotator `gold`.

**Evaluate** ("how good is it", "precision/recall", "compare methods"): `fb_evaluate()`. Report per
method:
- accuracy, macro-F1 and kappa for sentiment;
- precision and recall for the `negative` class;
- micro-F1 for aspects;
- the agreement between annotators A and B;
- the report file paths.

With no gold labels yet, `fb_evaluate(reference="agent")` compares methods with each other. Say
that this measures agreement, not accuracy.

Never invent labels, numbers or quotes. Report only what the tools returned.
