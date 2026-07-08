# Household Spend Management

As the manager of our household finances, I got tired of calculating how much
we'd actually spent, where it went, and whether everything was truly fair.
Most finance tools wanted too much access, made rigid assumptions about how
we spend, or took a lot of time to set up. The manual process in Excel became
such a burden that I stopped tracking entirely.

**I wanted a tool that fit how we actually live: sometimes disorganized,
occasionally last-minute, and definitely privacy-aware.** Most importantly,
it needed to be something both my partner and I could use — simple, and
producing accurate, trusted outputs.

**This project leans into the mess instead of running from it:**

- Statement exports from multiple banks and account types, in different formats
- Shared and personal expenses all mixed together
- Categories that show up on different cards each month
- Transparency on who paid for what

Its job: bring clarity to household money — what's spent, who paid, how to
settle up. Everything else — budgets, goals, forecasts — comes after. It's built on one insight: **trustworthy data comes before split
logic**. Import and clean first, verify against the statements themselves,
and only then decide how costs are shared.

---

## What it does

- **Import pipeline** — four statement formats (two credit-card exports with
  different schemas, one bank Visa, one chequing export), each with its own
  parser. Where a statement publishes its own total, the import reconciles
  the computed sum against it (tolerance $1.00) — a parser that can't
  reconcile isn't done.
- **Categorization** — a transparent two-layer system: a user-editable
  merchant-rules table (manual corrections always win) over source-provided
  category mapping. No ML — by design (see below).
- **Duplicate detection** — conservative: same-day/same-amount/same-merchant
  is flagged for human review, never auto-resolved.
- **Review UI** (Streamlit) — bulk categorization and duplicate review against
  the local database.
- **Conversational agent (read-only)** — a command-line agent answers questions
  over the verified data ("how much did we spend on groceries in June?", "who
  owes whom?"). Every number it reports comes from a deterministic tool, not the
  model's own arithmetic — the model selects tools and narrates; a tool-output
  check locks those results against hand-computed values. The full design
  contract, including the not-yet-built write path, is in
  [docs/AGENT_SPEC.md](docs/AGENT_SPEC.md).

## Where AI is used — and where it deliberately isn't

Each component uses the tool its job demands:

- **Categorization: rules, not ML.** A household sees a few hundred unique
  merchants; a flat, user-editable rules table is fully transparent, and a
  correction sticks 100% of the time. A classifier would add opacity and a
  dependency for no benefit at this scale.
- **CSV parsing: deterministic.** Structured input; a model has nothing to add.
- **Conversational interface: LLM for language, deterministic tools for math.**
  Turning "how much on groceries in June" into the right query is what a model
  is good at; computing the dollar figure is not. So the agent chooses and
  narrates tool calls but never does the arithmetic — if it states a number,
  that exact number came from a tool result, and the tool math is locked by a
  deterministic check against hand-computed values.
- **PDF and photo extraction (planned next): LLM vision.** Some older
  statements only exist on paper — no export to download. Code can parse a
  CSV's rows and columns, but it can't read a photo; that takes a vision
  model. The model's output isn't trusted by default: first it's tested on
  sample statements where the correct answers are already known (with
  personal details masked), and anything it extracts must still add up to
  the statement's printed total — the same check every import passes.

## Key design decisions

The full log, with options and criteria, lives in [DECISIONS.md](DECISIONS.md).
The load-bearing ones:

- **Import-first, split-later.** Split rules on dirty data produce wrong
  numbers. Clean the dataset first; splits are the last layer.
- **Statement owner = payer.** Whoever's statement it is paid that bill —
  it's a fact, not a guess. Who's *responsible* for a cost is a separate
  question, answered later by category splits.
- **Raw source text is always preserved.** The original statement line is
  stored next to the cleaned-up version, so any transaction can be traced
  back to exactly what the bank recorded.
- **Conservative duplicate detection.** Auto-deleting a real transaction
  corrupts totals silently; reviewing a false positive costs seconds. Design
  for the cheap failure.

---

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p data
cp seed_config.example.json data/seed_config.json
```

Edit `data/seed_config.json` with your household: people, accounts, the
statement files you'll import (with each statement's total, to enable the
reconciliation check), and any starter merchant rules. Then:

```bash
.venv/bin/python src/schema.py     # initialize the local database
# drop your statement exports into data/, then:
.venv/bin/python src/importer.py   # import + categorize + reconcile
.venv/bin/python src/dedupe.py     # flag suspected duplicates
.venv/bin/streamlit run src/app.py # review UI
```

## Privacy

All financial data — statements, exports, the database — lives in `data/` and
is git-ignored. Processing is local. LLM extraction development uses only
redacted samples; real statements never leave the machine.
