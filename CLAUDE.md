# Project instructions — household-spend

Working conventions for this repo. All contributions (human or AI-assisted) follow these rules.

---

## Build principle

Use the simplest solution that does the job; add complexity — including AI/ML —
only when a demonstrated need exceeds what the simpler approach can do. The
problem and the data choose the tool, not the other way around. Every escalation
in complexity gets a `DECISIONS.md` entry justifying it.

---

## Git workflow

Feature-branch + PR model. `main` is always releasable.

### Branch naming
```
<type>/<short-description>
```
Types: `feature/` (new capability), `improve/` (refinement of existing behavior), `fix/` (bug).

### The loop
1. Branch from `main` — never commit directly to it (initial scaffolding excepted)
2. Commit in imperative present tense: "Add X", "Fix Y"; first line ≤ 72 chars
3. Open a PR — one PR = one meaningful capability, not one PR per change
4. Squash-merge and delete the branch

### PR descriptions
- 2–3 bullets: what it delivers, stated user-facing
- One sentence: why it matters / what it unblocks
- Design rationale does not go in the PR — it goes in `DECISIONS.md`

## Decision log

`DECISIONS.md` is curated, not exhaustive: an entry must document a real
trade-off — a hard call that was costly to get wrong. Routine choices and
engineering necessities don't qualify. Entries carry context, the decision,
criteria, and revisit conditions, and should be scannable in ~30 seconds —
decision first, tight prose, no process narrative.

---

## Data security — non-negotiable

Financial data never enters git.

- `data/` — all statement files, exports, working copies
- `*.db`, `*.sqlite*` — the local database
- `*.csv`, `*.pdf`, `*.png`, `*.jpg`, `*.jpeg` — statement files in any format
- `.env` — API keys, always loaded from environment, never hardcoded

All of the above are git-ignored. Before every commit, read `git status --short`
line by line; if anything from `data/` or a database file appears, stop and
investigate why `.gitignore` didn't catch it.

No personal identifiers (names, initials, account numbers, local filesystem
paths) in any committed file. Person and account data are seeded locally,
never hardcoded in source.

### LLM extraction boundary
When LLM vision is used for statement parsing: parser development uses only
redacted samples (masked card numbers, generic amounts). Real statements are
processed locally by running the code — they do not pass through hosted
AI sessions.

---

## Validation requirements

Scoped by the kind of change:

- **Import parsers:** a new or modified parser must be validated against its
  real source before it's done. Where the statement publishes its own total,
  that means reconciling the computed sum against it (tolerance: $1.00, per
  `DECISIONS.md`). Where the source has no printed total (e.g. annual
  exports), use the strongest available check — row count against the source
  file, spot-checked amounts — and record which check was used.
- **LLM extraction:** accuracy-tested against a labeled test set of redacted
  sample statements (field-level accuracy) before running on real data, and
  gated by the same reconciliation check at import time.
- **UI changes:** manual QA in a real browser — click through the changed
  flow before calling it done, don't assume correctness from reading code.
- **Schema changes:** regression-checked as behavior-preserving — capture
  category totals before branching, re-run the full import after, and compare.
  The automated PRAGMA hook (`foreign_key_check` + `integrity_check`) runs on
  every schema.py edit and checks that the database structure is intact. It
  does not check whether query results or spend totals are correct. Both checks
  are required.
- **Money math (settlement, reporting):** ships with automated test cases
  using hand-computed expected results, run as regression tests on every
  change. Cover edge cases: refunds, 100/0 splits, excluded payments. A
  wrong settlement number looks identical to a right one — the one failure
  mode manual QA can't catch.
