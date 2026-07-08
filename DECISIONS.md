# Decision log

Non-obvious calls made during design and build: the options considered, the
criteria, and the conditions under which each decision should be revisited.
Most recent first.

---

## [2026-07-07] The agent never computes money — tool-narration architecture

Every number the agent states — spend totals, settlement amounts, counts — comes
from a deterministic tool result, not from the model's own arithmetic.

**Options considered:**
1. Agent computes derived figures (e.g. per-transaction average, % of total) from
   tool results in the same response.
2. Agent narrates only what tools return; any derived figure requires a tool call.

**Decision:** option 2, enforced via prompt and verified in S01–S08.

**Trade-off:** chattier flows (an "average per transaction" question requires a list
call, not a division); slower for multi-step derived questions. Against: a model
doing arithmetic looks right most of the time but can hallucinate, round wrong, or
misattribute a figure. The tool result is the audit trail — no tool result, no audit
trail. For a financial tool, silent arithmetic errors are the worst failure mode.

**Revisit if:** user research shows the narration-only constraint produces responses
so constrained they're unhelpful, and the error rate on model arithmetic in evals
is demonstrably low.

---

## [2026-06-30] A single correction creates a merchant rule

**Decision:** One manual correction immediately creates a rule (individual
transactions can be excluded from the bulk apply) — no waiting for repeated
corrections.

**Criteria:** faster trust-building outweighs overgeneralization risk:
mis-categorization is cheap to fix, and a newer correction always overrides
an older rule.

**Revisit if/when:** single-correction rules visibly misfire on unrelated
transactions.

---

## [2026-06-30] Duplicate detection is conservative: flag for review, never auto-resolve

**Decision:** Same-date/amount/merchant/owner groups are flagged as
suspected duplicates for human review. Nothing is auto-deleted or
auto-merged — deleting a real transaction corrupts totals silently, while
reviewing a false positive costs seconds.

**Validation on real data:** ~2.5 years of statements produced 15 flagged
groups, almost all legitimate non-duplicates (hotel nights at one rate,
repeated subscription charges, same-day parking). An aggressive matcher
would have auto-deleted real transactions.

**Revisit if/when:** flagged-group volume grows enough that review becomes a
burden — tighten the match criteria before ever considering auto-resolution.

---

## [2026-06-30] Statement-provided categories as the default; user rules always override

**Decision:** First match wins, checked in this order:
1. the user's merchant rules (seed rules + every review correction)
2. the category the statement itself provides, translated into our taxonomy
3. Uncategorized — queued for review; the user's pick becomes a new rule

The user's choices permanently outrank the statement's.

**Context:** writing a rule for every merchant is endless upkeep (one export
had 100+ unique merchants). Each statement source only uses ~30 categories
of its own — translate that short list once, and it covers the long tail of
merchants automatically.

**Revisit if/when:** a source's categories prove unreliable — demote them to
a weaker signal or drop that mapping.

---

## [2026-06-30] Non-spend is excluded by transaction type

**Decision:** `payment` is currently the only recognized non-spend type —
those transactions stay out of all spend math. To extend: transfers (when
they appear in imported data, e.g. card bills paid from chequing) and
proper refund handling (matching a refund to its original purchase; today
refunds simply net against category totals).

**Context:** before this, card payments were leaking into spend totals —
fixing it moved the computed total from $41,309.62 to $64,793.44 on the
same data.

**Revisit if/when:** each new source's parser decides how to determine the
type (some sources provide it; Amex is inferred from merchant text) — extend
the recognized non-spend types as new ones show up.

---

## [2026-06-29] Schema: 4 core tables now, 10 deferred

**Decision:** Implement `persons`, `accounts`, `import_files`,
`transactions`. Defer everything else until real data forces it.

**Context:** planning produced a 14-entity schema. Building it all before
importing one real statement optimizes for the wrong risk — the riskiest
assumption was whether varied formats parse cleanly, not whether the schema
scales.

**Criteria:** schema changes on a single-user local SQLite file are cheap;
over-designing now costs more than migrating later.

**Revisit if/when:** deferral is sequencing, not refusal — tables get added
as concrete needs arrive (as happened with `merchant_rules` and the
duplicate columns).

---

## [2026-06-29] Reconciliation tolerance: $1.00

**Decision:** Import totals within $1.00 of the statement total pass; anything
≥ $1.00 requires investigation before the import counts.

**Context:** a real export summed $0.95 under its statement total. Attributed
to export rounding without line-by-line investigation — at 0.06% of the
statement, the gap is below any materiality threshold worth the time.

**Revisit if/when:** the same sub-$1 gap recurs across imports from one
source — that pattern suggests a systematically omitted fee, not rounding.

---

## [2026-06-29] Categorization: rules table, not ML

**Decision:** A flat, user-editable `merchant pattern → category` table. No
classifier, no embeddings.

**Criteria:** a household sees a few hundred unique merchants — comfortably
rules-table scale. The table is fully transparent (you can see exactly why a
transaction was categorized) and corrections stick 100% of the time.
"Learning" means the user's correction becomes a rule applied on the next
import — explicit, not opaque. For financial data, trust beats automation.

**Revisit if/when:** merchant strings across providers get inconsistent
enough to hurt — normalization logic would come first, still not ML. Or if
sources stopped providing their own categories at real scale: the rules
table depends on source-category mapping carrying the bulk load. In that
case I'd consider LLM-suggested, human-confirmed categorization feeding the
rules table — the model as one-time scaffolding, not the categorizer of
record.

---

## [2026-06-29] Statement owner = payer

**Decision:** The account owner is treated as the payer of every transaction
on their statement. No override mechanism.

**Criteria:** payer and responsibility are separate concerns, and the model
already separates them. Payer is a fact — whoever owns the card pays its
bill, even when the other person made the purchase. Responsibility is
handled at split time: a purchase made on the other person's card gets a
category whose split assigns the cost to the actual spender, and settlement
nets correctly. The apparent edge case dissolves without a new mechanism.

**Revisit if/when:** the payer fact itself is wrong — e.g. one person paying
the other's statement bill — would justify a per-transaction payer override.
Not built until that actually occurs.
