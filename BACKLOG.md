# Backlog

Parking lot for out-of-scope ideas surfaced mid-slice — captured here instead
of expanding the work in progress. Nothing here is committed work; an item
graduates into scoped work only when it's picked up.

## Deferred agent capability (v1 non-goals — see [docs/AGENT_SPEC.md](docs/AGENT_SPEC.md))

- Multi-month and natural-language date-range queries ("last quarter")
- Bulk operations across many transactions in one action
- Editing splits, categories, or accounts through the agent
- Undo after confirmation (discard-before-confirm is the undo; a post-confirm
  correction is a new proposal)

## Import review before mass import

- **First-import dry-run / preview.** Before committing a new statement format's
  rows, a `--dry-run` mode that parses and categorizes in memory (commits
  nothing) and prints: reconciliation (computed vs statement total, PASS/FAIL);
  a provenance breakdown (N via merchant rules, M via source-category map, K
  uncategorized); the distinct source-label → our-category pairs the import will
  apply, with counts; and the unmapped source labels (which land uncategorized).
  Lets the source-category vocabulary map be reviewed before it reaches
  settlement. CLI first (build principle: simplest thing that does the job); a
  staging UI only if it earns one. Surface provenance *tiers*
  (`merchant_rule` > `source_mapped` > `none`) rather than inventing a
  confidence score there is no basis to compute.

- **Revisit: mapping differs by whether the source provides category labels.**
  Two structurally different cases the dry-run must handle distinctly:
  - *Labels provided* (e.g. Wealthsimple): the source ships its own category
    vocabulary, so the reviewable unit is the source-label → our-category map.
    Misses already fall through to uncategorized; the uncaught risk is a label
    that maps but maps *wrong*.
  - *No labels* (raw merchant only): nothing to map — categorization rests on
    merchant rules, and everything unmatched is uncategorized. The reviewable
    unit is the merchant-rule coverage, not a vocabulary map.
  Decide whether these share one preview surface or diverge, and how each
  signals what needs review.

## Test quality

- **`check_report_queries()` structural gap.** The function tests inline SQL that
  mimics `report.py`'s WHERE clause rather than calling `report.report()` directly.
  `SIGNED_AMOUNT` is imported from `report.py` so the amount expression is anchored,
  but the JOIN structure isn't — if the INNER JOIN regressed, this assertion would
  still pass. Fix: split `report.py`'s query logic from the print calls so it can
  be called and introspected, or capture stdout and assert on it. (Surfaced: Group A
  light retro, 2026-07-14.)

## New tool ideas

- None yet — the v1 catalog is fixed at seven tools; new ideas land here first.
