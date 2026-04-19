# Shopping Search Eval Pipeline

Dev-time feedback loop for iterating on shopping-search query quality. Lives in
`backend/shopping_eval/`. Independent from the older image-generation eval
(`docs/EVAL_PIPELINE.md`, `backend/tests/eval/`) — same idea, different signal.

## When to use it

Reach for this when you want to change *how products are searched* — editing
`_build_search_queries_tagged` in `app/activities/shopping.py`, tuning the
`synthetic_listing.txt` prompt, adjusting the retailer allow-list, etc. — and
need to know whether the change helps, hurts, or is a wash before shipping.

Not relevant for: generation pipeline (use the image eval), intake agent,
LiDAR, or anything outside the Exa search path.

## The tuning loop

```
1.  python -m shopping_eval baseline           # snapshot current quality
2.  edit a prompt or query-builder             # change something
3.  python -m shopping_eval compare            # print per-metric delta
4.  keep the edit (commit) or git-restore it   # CLI does NOT touch git
5.  python -m shopping_eval ablation           # per-component success rates
6.  use ablation hints to pick the next edit → loop
```

The CLI prints a verdict (`IMPROVED` / `NEUTRAL` / `REGRESSED`) but does not
auto-accept or auto-revert. You own that decision.

## Metrics (all deterministic, no LLM, $0 per run)

| Metric | What it measures | How it fails |
|---|---|---|
| `link_alive_rate` | Fraction of Exa result URLs where `HTTP HEAD` returns 200–399 | Retailer link rot; bad domain; Exa returns stale results |
| `product_match_rate` | Fraction where Exa's `product_name` / page text contains a category keyword (`sofa`, `coffee table`, …) | Query returns the wrong *type* of product (candle when asking for a sofa) |
| `dimension_match_rate` | Fraction where parsed product dimensions fit the room constraint within 15% tolerance | Query returns a sofa that won't fit the room |

**Inconclusive handling:** dimension-match returns `None` when the product has
no dimension string, the category has no size constraint (lamps, pillows, wall
art, planter), or room data is missing. `None` is *excluded from the average*
— so low `dimension_match_rate` can mean either "products don't fit" or
"effective N is small." Check `per_category.num_queries` to disambiguate.

Product-match is keyword-only on purpose. Style / color / material match
belongs to the live product-scorer; this check only catches gross category
mismatches so the loop's signal isn't drowned out by judgment calls.

## CLI reference

```bash
# All commands run from backend/ with the virtualenv activated.

# Default strategy = tagged queries (what _build_search_queries_tagged produces).
python -m shopping_eval baseline
python -m shopping_eval compare
python -m shopping_eval ablation

# Score-Then-Search: Claude drafts a synthetic listing, that text is the Exa query.
python -m shopping_eval baseline --strategy synthetic
python -m shopping_eval baseline --strategy both        # tagged + synthetic in one run

# The three strategies:
#   tagged     → only _build_search_queries_tagged (default, no Claude calls)
#   synthetic  → only generate_synthetic_listing (Claude + Exa)
#   both       → both, so ablation can A/B them against each other
```

### Required env vars

| Var | When | Why |
|---|---|---|
| `EXA_API_KEY` | Always | Every strategy searches via Exa |
| `ANTHROPIC_API_KEY` | `--strategy synthetic` or `both` | Score-Then-Search calls Claude to draft the listing |
| `EXA_CACHE_DIR` | Recommended | Directory path; file-caches Exa responses. Makes re-runs $0. |
| `SYNTHETIC_LISTING_CACHE_DIR` | Recommended with synthetic strategies | Caches Claude-drafted listings. Same deal. |

### Output files

```
backend/shopping_eval/results/
├── baseline.json        # last run's aggregate scores, written by `baseline`
└── ablation.jsonl       # append-only; one row per (trial × component)
```

`results/` is gitignored. Delete freely.

## Ablation report — how to read it

`python -m shopping_eval ablation` prints per-category rows like:

```
SOFA:
  description            n=10  link=90%  product=80%
  material_focused       n=10  link=90%  product=60%
  style_context          n=10  link=90%  product=50%
  room_constrained       n=10  link=90%  product=40%
```

Rows are sorted by `product_rate` descending. Higher = that component pulls
better products for this category. Use this to spot wins (keep the top
component, try harder on the bottom) and hedge against overfitting (a
component that helps sofas but tanks lamps is a net neutral).

Component names come straight from `_build_search_queries_tagged`:
`description`, `style_context`, `brief_mood`, `brief_room`, `source_reference`,
`material_focused`, `dimension_query`, `color_synonym`, `room_constrained`, and
(for the synthetic strategy) `synthetic_listing`.

## Benchmark fixtures

`backend/shopping_eval/fixtures/benchmark_items.json` — 10 cases spanning sofa,
sectional, coffee table, rug, floor lamp, pendant, curtains, wall art,
bookshelf, planter. Each case is an extracted-item dict plus optional
`design_brief_json` / `room_dimensions_json` for style and dimension context.

Adding a case:

```jsonc
{
  "item_id": "bench_mirror_01",
  "item": {
    "category": "mirror",
    "description": "Round brass-framed mirror over a console table",
    "style": "mid-century",
    "material": "brass",
    "color": "antique gold",
    "estimated_dimensions": "36 inch diameter",
    "source_tag": "IMAGE_ONLY"
  },
  "expected_category": "mirror",
  "expected_material": "brass",
  "expected_dimensions": "36 inch",
  "gold_url": null,
  "design_brief_json": { ... },
  "room_dimensions_json": { "width_m": 4.0, "length_m": 5.0, "height_m": 2.7 }
}
```

Keep fixtures product-like, not edge-case-y — the loop measures typical-case
quality, not adversarial robustness.

## Production code relationship

Three functions in `app/activities/shopping.py` are exercised here:

| Function | Called by prod? | Called by benchmark? | Notes |
|---|---|---|---|
| `_build_search_queries` | Yes (`generate_shopping_list`) | No | Current production query builder |
| `_build_search_queries_tagged` | No | Yes (`--strategy tagged` / `both`) | Same intent + component tags for ablation |
| `build_synthetic_listing_query` | No | Indirectly, via `generate_synthetic_listing` (`--strategy synthetic` / `both`) | Returns the *prompt*, not the query |

**Drift risk.** The tagged and untagged builders are not unified yet. Changes
to one must be mirrored into the other until a follow-up collapses them into a
single implementation. If you modify `_build_search_queries` directly, the
benchmark will not reflect it.

## Gotchas

- **Exa cache key** includes `num_results`, `search_type`, `include_domains`,
  and `include_text` — so dual-pass searches don't collide. If you change any
  of these, the cache is effectively cold again.
- **Claude model** for synthetic listings is read from `SHOPPING_EVAL_CLAUDE_MODEL`
  (default: `claude-opus-4-5`). Overriding the model changes the cache key, so
  re-runs after a model bump are *not* free.
- **Ablation log is append-only.** It never forgets. Wipe
  `results/ablation.jsonl` when the underlying component taxonomy changes
  (e.g., you renamed `material_focused`), or the report will mix the old and
  new schemas.
- **Strategy = `both` doubles query volume** per case. Factor that into cost
  estimates when the Exa cache is cold.

## Test suite

```bash
.venv/bin/python -m pytest tests/shopping_eval/ -q
```

24 tests cover the three checks, the trial runner, the ablation logger, the
benchmark runner, and the synthetic-listing wiring. All external calls
(Exa, Claude, HTTP HEAD) are mocked — no API keys required.
