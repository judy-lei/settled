# Household Spend Management

As the manager of our household finances, I got tired of calculating how much
we'd actually spent, where it went, and whether everything was truly fair.
Most finance tools wanted too much access, made rigid assumptions about how
we spend, or took a lot of time to set up. The manual process in Excel became
such a burden that I stopped tracking entirely.

**I wanted a tool that fit how we actually live: sometimes disorganized,
occasionally last-minute, and not willing to hand our accounts over to a third
party.** Most importantly, it needed to produce numbers both my partner and I
could actually trust.

**This project leans into the mess instead of running from it:**

- Statement exports from multiple banks and account types, in different formats
- Shared and personal expenses all mixed together
- Categories that show up on different cards each month
- Transparency on who paid for what

Its job: bring clarity to household money, what's spent, who paid, how to
settle up. Everything else, budgets, goals, forecasts, comes after.
**Trustworthy data comes before split logic.** Import and clean first, verify
against the statements themselves, then decide how costs are shared.

---

## What it does

- **Import pipeline** — four statement formats (two credit-card exports with
  different schemas, one bank Visa, one chequing export), each with its own
  parser. Where a statement publishes its own total, the import reconciles
  the computed sum against it (tolerance $1.00). A parser that can't
  reconcile isn't done.
- **Categorization** — a transparent two-layer system: a user-editable
  merchant-rules table (manual corrections always win) over source-provided
  category mapping. No ML.
- **Duplicate detection** — same-day, same-amount, same-merchant is flagged
  for human review, not auto-resolved.
- **Review UI** (Streamlit) — bulk categorization and duplicate review against
  the local database.
- **Conversational agent (read-only)** — a command-line agent that answers
  questions over the verified data ("how much did we spend on groceries in
  June?", "who owes whom?"). The model selects and narrates tool calls; every
  number it reports comes from a deterministic tool. The full design contract,
  including the write path still in progress, is in
  [docs/AGENT_SPEC.md](docs/AGENT_SPEC.md).

## Where AI fits

The working principle: use the simplest tool that can do the job.

Categorization and CSV parsing are deterministic. The input is structured,
the rules are user-editable, and a correction needs to stick 100% of the
time. A model adds nothing and would require a dependency the problem doesn't
justify.

The conversational agent is where a model earns its place. Turning "how much
on groceries in June?" into the right database query is exactly what it's
good at. Computing the number is not, so the agent chooses and narrates tool
calls but doesn't do arithmetic. A tool-output check locks every reported
number against hand-computed values.

PDF and photo extraction is next. Some older statements only exist on paper.
The model's output gets tested against labeled samples where the correct
answers are already known, and anything extracted still has to reconcile
against the statement total, the same check every import already passes.

## Key design decisions

The full log, with options and criteria, lives in [DECISIONS.md](DECISIONS.md).
The load-bearing ones:

- **Import-first, split-later.** Split rules on dirty data produce wrong
  numbers. Clean the dataset first; splits are the last layer.
- **Statement owner = payer.** Whoever's statement it is paid that bill.
  It's a fact, not a guess. Who's *responsible* for a cost is a separate
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
