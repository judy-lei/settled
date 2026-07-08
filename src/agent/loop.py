"""
Household-spend agent — CLI REPL.

Usage:
    .venv/bin/python src/agent/loop.py              # real DB (data/spend.db)
    .venv/bin/python src/agent/loop.py --eval       # fixture DB (evals/fixtures/eval.db)
    AGENT_MODEL=claude-sonnet-4-6 .venv/bin/python src/agent/loop.py

Transcripts are written to data/transcripts/<conversation_id>.jsonl (gitignored).
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic

_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from agent.tools_read import get_settlement, list_uncategorized, query_spend
from schema import get_conn

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = os.environ.get("AGENT_MODEL", "claude-haiku-4-5-20251001")
PROMPTS_DIR = _ROOT / "prompts"
TRANSCRIPTS_DIR = _ROOT / "data" / "transcripts"
EVAL_DB = _ROOT / "evals" / "fixtures" / "eval.db"

TOOL_DEFINITIONS = [
    {
        "name": "query_spend",
        "description": (
            "Aggregate or list spend transactions for a calendar month. "
            "Use for questions about how much was spent, optionally filtered "
            "by category or by person. mode='list' returns individual transactions "
            "(capped at 50); mode='total' returns the aggregate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Calendar month in YYYY-MM format, e.g. '2026-06'.",
                },
                "category": {
                    "type": "string",
                    "description": "Optional. Filter to this category name (must match exactly).",
                },
                "owner": {
                    "type": "string",
                    "description": "Optional. Filter to this person's transactions (must match display_name exactly).",
                },
                "mode": {
                    "type": "string",
                    "enum": ["total", "list"],
                    "description": "total (default) returns aggregate; list returns individual rows.",
                },
            },
            "required": ["period"],
        },
    },
    {
        "name": "get_settlement",
        "description": (
            "Calculate who owes whom for a calendar month. Returns each person's "
            "paid amount, fair share, balance, and the settlement transfer direction "
            "and amount. Always check uncategorized_count in the result before "
            "stating the settlement as final."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Calendar month in YYYY-MM format, e.g. '2026-06'.",
                },
            },
            "required": ["period"],
        },
    },
    {
        "name": "list_uncategorized",
        "description": (
            "List uncategorized spend transactions (no category assigned). "
            "Use when the user wants to know what needs to be categorized, "
            "or to investigate why a settlement total looks incomplete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Optional. Limit to a calendar month in YYYY-MM format. Omit to return all uncategorized.",
                },
            },
            "required": [],
        },
    },
]


# ---------------------------------------------------------------------------
# Transcript logging
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Transcript:
    def __init__(self, conversation_id: str) -> None:
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        self._path = TRANSCRIPTS_DIR / f"{conversation_id}.jsonl"
        self._turn = 0
        self._cid = conversation_id

    def _write(self, event: dict) -> None:
        with open(self._path, "a") as f:
            f.write(json.dumps(event) + "\n")

    def meta(self, model: str, prompt_version: str, db: str) -> None:
        """First event: records what produced this conversation. Reliability
        evidence is model- and prompt-specific, so the audit trail must carry
        both (AGENT_SPEC invariant 6; EVAL_SPEC §5 report provenance)."""
        self._write({"ts": _now(), "conversation_id": self._cid,
                     "role": "meta", "model": model,
                     "prompt_version": prompt_version, "db": db})

    def user(self, content: str) -> None:
        self._turn += 1
        self._write({"ts": _now(), "conversation_id": self._cid,
                     "turn": self._turn, "role": "user", "content": content})

    def tool_call(self, tool_name: str, args: dict, result: dict) -> None:
        self._write({"ts": _now(), "conversation_id": self._cid,
                     "turn": self._turn, "role": "tool_call",
                     "tool_name": tool_name, "args": args, "result": result})

    def assistant(self, content: str) -> None:
        self._write({"ts": _now(), "conversation_id": self._cid,
                     "turn": self._turn, "role": "assistant", "content": content})


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

def dispatch(conn: sqlite3.Connection, tool_name: str, args: dict) -> dict:
    try:
        if tool_name == "query_spend":
            return query_spend(
                conn,
                period=args["period"],
                category=args.get("category"),
                owner=args.get("owner"),
                mode=args.get("mode", "total"),
            )
        if tool_name == "get_settlement":
            return get_settlement(conn, period=args["period"])
        if tool_name == "list_uncategorized":
            return list_uncategorized(conn, period=args.get("period"))
        return {"error": f"Unknown tool: {tool_name}"}
    except sqlite3.Error as e:
        return {"error": f"Database error in {tool_name}: {e}"}
    except KeyError as e:
        return {"error": f"Missing required argument for {tool_name}: {e}"}


# ---------------------------------------------------------------------------
# Main REPL
# ---------------------------------------------------------------------------

PROMPT_FILE = PROMPTS_DIR / "agent_v1.md"


def run(db_path: Path) -> None:
    system_prompt = PROMPT_FILE.read_text()
    client = anthropic.Anthropic()
    conversation_id = str(uuid.uuid4())
    transcript = Transcript(conversation_id)
    transcript.meta(MODEL, PROMPT_FILE.stem, db_path.name)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    messages: list[dict] = []

    print(f"Household spend agent  (model: {MODEL}  db: {db_path.name})")
    print("Type 'quit' or Ctrl-C to exit.\n")

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break

            transcript.user(user_input)
            messages.append({"role": "user", "content": user_input})

            # Tool-calling loop for this turn
            while True:
                response = client.messages.create(
                    model=MODEL,
                    system=[{"type": "text", "text": system_prompt,
                             "cache_control": {"type": "ephemeral"}}],
                    tools=TOOL_DEFINITIONS,
                    messages=messages,
                    max_tokens=1024,
                )

                if response.stop_reason == "end_turn":
                    text = next(
                        (b.text for b in response.content if hasattr(b, "text")), ""
                    )
                    messages.append({"role": "assistant", "content": response.content})
                    transcript.assistant(text)
                    print(f"\nAgent: {text}\n")
                    break

                if response.stop_reason == "tool_use":
                    tool_uses = [b for b in response.content if b.type == "tool_use"]
                    if not tool_uses:
                        print(f"[stop_reason=tool_use but no tool_use blocks]")
                        break
                    messages.append({"role": "assistant", "content": response.content})

                    tool_results = []
                    for tu in tool_uses:
                        result = dispatch(conn, tu.name, tu.input)
                        transcript.tool_call(tu.name, dict(tu.input), result)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": json.dumps(result),
                        })

                    messages.append({"role": "user", "content": tool_results})
                    continue

                # Unexpected stop reason
                print(f"[unexpected stop_reason: {response.stop_reason}]")
                break

    except KeyboardInterrupt:
        print("\nExiting.")
    finally:
        conn.close()
        print(f"Transcript: {transcript._path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Household spend agent")
    parser.add_argument(
        "--eval", action="store_true",
        help="Run against the fixture eval DB instead of the real DB"
    )
    args = parser.parse_args()

    if args.eval:
        db = EVAL_DB
        if not db.exists():
            sys.exit(f"Eval DB not found. Run: .venv/bin/python evals/fixtures/build_fixture.py")
    else:
        from schema import DB_PATH
        db = DB_PATH

    run(db)
