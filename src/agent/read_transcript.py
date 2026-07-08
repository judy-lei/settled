"""
Pretty-print a conversation transcript for human review in VS Code.

Usage:
    .venv/bin/python src/agent/read_transcript.py           # most recent transcript
    .venv/bin/python src/agent/read_transcript.py <path>    # specific file
"""

import json
import sys
from pathlib import Path

TRANSCRIPTS_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"


def format_transcript(path: Path) -> str:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not events:
        return "(empty transcript)"

    lines = [f"Transcript: {path.name}", f"Model conversation — {len(events)} events", ""]

    current_turn = None
    for e in events:
        role = e["role"]

        if role == "meta":
            lines.append(
                f"[model: {e.get('model', '?')}  "
                f"prompt: {e.get('prompt_version', '?')}  "
                f"db: {e.get('db', '?')}]"
            )
            lines.append("")
            continue

        turn = e.get("turn", "?")

        if turn != current_turn:
            current_turn = turn
            lines.append(f"{'─' * 60}")
            lines.append(f"Turn {turn}")
            lines.append("")

        if role == "user":
            lines.append(f"[YOU]")
            lines.append(e["content"])
            lines.append("")

        elif role == "tool_call":
            lines.append(f"[TOOL] {e['tool_name']}")
            lines.append(f"  args:   {json.dumps(e['args'], indent=2).replace(chr(10), chr(10) + '  ')}")
            lines.append(f"  result: {json.dumps(e['result'], indent=2).replace(chr(10), chr(10) + '  ')}")
            lines.append("")

        elif role == "assistant":
            lines.append(f"[AGENT]")
            lines.append(e["content"])
            lines.append("")

    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) > 1:
        files = [Path(sys.argv[1])]
    else:
        files = sorted(TRANSCRIPTS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
        if not files:
            sys.exit(f"No transcripts found in {TRANSCRIPTS_DIR}")

    sections = []
    for i, path in enumerate(files, 1):
        header = f"{'═' * 60}\nConversation {i} of {len(files)} — {path.name}\n{'═' * 60}\n"
        sections.append(header + format_transcript(path))

    out = TRANSCRIPTS_DIR / "review.txt"
    out.write_text("\n\n".join(sections))
    print(f"Written {len(files)} transcript(s) to {out}")


if __name__ == "__main__":
    main()
