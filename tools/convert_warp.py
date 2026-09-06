"""Convert Warp local AI history to markdown.

Source: %LOCALAPPDATA%/warp/Warp/data/warp.sqlite (copy the file first —
Warp holds a WAL lock while running; sqlite3 can read a copied file cleanly).

Tables used (observed 2025-10 DB):
  ai_queries(exchange_id, conversation_id, start_ts, working_directory,
             input, output_status, model_id)   — input is tool-result JSON
  ai_blocks(exchange_id, output)               — output is {"Received":{"output":
    [{"Text":{"text":...assistant narrative...}},
      {"Action":{"id":...,"action_type":{ToolName:{...}}}}}, ...]}}
  agent_conversations(conversation_id, conversation_data, last_modified_at)

LIMITATION (honest, not fixable locally): user prompts live server-side and
are NOT in this sqlite file. Each .md therefore records the assistant
narrative + a compact tool-action summary, with cwd/timestamps/model so the
session stays useful without inventing the missing user text.

Usage:
  python convert_warp.py [--db PATH] [--out DIR]
Writes one .md per conversation_id to <out>/warp/.
"""
import json
import sys
import os
import shutil
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path

OUT_DEFAULT = Path(r"C:\SecondBrainStaging\SecondBrain\chats")
DB_DEFAULT = Path(os.environ.get("LOCALAPPDATA", "")) / "warp" / "Warp" / "data" / "warp.sqlite"


def slugify(text, maxlen=60):
    import re
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:maxlen].rstrip("-") or "untitled"


def write_md(out_dir, date, title, lines):
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{date}_{slugify(title)}.md"
    path = out_dir / fname
    n = 1
    while path.exists():
        n += 1
        path = out_dir / f"{date}_{slugify(title)}-{n}.md"
    body = f"# {title}\n\n_Date: {date}_\n\n" + "\n\n".join(lines) + "\n"
    path.write_text(body, encoding="utf-8")
    return path


def summarize_action(action):
    """Compact one-line summary of a Warp Action dict."""
    if not isinstance(action, dict):
        return None
    at = action.get("action_type")
    if isinstance(at, dict) and at:
        tool = next(iter(at))
        detail = at[tool]
        extra = ""
        if isinstance(detail, dict):
            q = detail.get("query") or detail.get("search_dir") or ""
            f = detail.get("file_names") or detail.get("patterns") or ""
            if isinstance(f, list):
                f = ", ".join(str(x)[-60:] for x in f[:3])
            extra = str(q or f)[:120]
        return f"[tool: {tool}" + (f" — {extra}" if extra else "") + "]"
    return None


def block_texts(output_json):
    texts, tools = [], []
    try:
        data = json.loads(output_json)
    except (json.JSONDecodeError, TypeError):
        return texts, tools
    for item in data.get("Received", {}).get("output", []):
        if "Text" in item and isinstance(item["Text"], dict):
            t = (item["Text"].get("text") or "").strip()
            if t:
                texts.append(t)
        elif "Action" in item and isinstance(item["Action"], dict):
            s = summarize_action(item["Action"])
            if s:
                tools.append(s)
    return texts, tools


def run(db_path=DB_DEFAULT, out_root=OUT_DEFAULT):
    db_path = Path(db_path)
    out = Path(out_root) / "warp" if Path(out_root).name != "warp" else Path(out_root)
    # Copy aside so Warp's WAL lock can't bite us.
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "warp.sqlite"
        shutil.copy2(db_path, tmp)
        db = sqlite3.connect(tmp)
        queries = list(db.execute(
            "SELECT exchange_id, conversation_id, start_ts, working_directory,"
            " output_status, model_id FROM ai_queries ORDER BY start_ts"))
        blocks = dict(db.execute("SELECT exchange_id, output FROM ai_blocks"))
        db.close()
    by_conv = defaultdict(list)
    for eid, cid, ts, cwd, status, model in queries:
        by_conv[cid].append((eid, ts, cwd, status, model))
    count = 0
    for cid, exchanges in sorted(by_conv.items()):
        cwd = exchanges[0][2] or ""
        date = (exchanges[0][1] or "undated")[:10]
        model = exchanges[0][4] or ""
        lines = [f"_Source: warp conversation {cid} | cwd: {cwd} | model: {model}_",
                 "_Note: user prompts are server-side only; local DB holds "
                 "assistant narrative + tool trace._"]
        for eid, ts, _, status, _ in exchanges:
            texts, tools = block_texts(blocks.get(eid, ""))
            if not texts and not tools:
                continue
            lines.append(f"_{ts} [{status}]_")
            for t in texts:
                lines.append(f"**Warp**:\n{t}")
            for s in tools:
                lines.append(s)
        if len(lines) <= 2:
            continue
        short_cwd = cwd.rstrip("\\/").split("\\")[-1] if cwd else cid[:8]
        write_md(out, date, f"warp {short_cwd} {date}", lines)
        count += 1
    print(f"warp: converted {count} conversations to {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    db = Path(args[args.index("--db") + 1]) if "--db" in args else DB_DEFAULT
    out = Path(args[args.index("--out") + 1]) if "--out" in args else OUT_DEFAULT
    run(db, out)
