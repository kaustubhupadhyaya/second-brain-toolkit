"""Convert Cursor + VS Code Copilot Chat local sessions to markdown.

Both tools store the SAME JSON schema (observed live files 2025-2026):
  <workspaceStorage>/<hash>/chatSessions/<uuid>.json
  {"version":3, "requesterUsername":..., "requests":[
     {"message":{"text":...user...},
      "response":[{"value":"...assistant markdown..."}, ...]},
     ...]}

Sources parsed:
  Cursor:         %APPDATA%/Cursor/User/workspaceStorage/*/chatSessions/*
  VS Code Copilot:%APPDATA%/Code/User/workspaceStorage/*/chatSessions/*

The assistant `value` strings can embed VS Code URI JSON noise; only plain
string values are kept. File mtime is used as the date (schema has no
per-message timestamps).

Usage:
  python convert_copilot_chat.py [--out DIR]
Writes to <out>/cursor/ and <out>/vscode-copilot/.
"""
import json
import sys
import os
from datetime import datetime
from pathlib import Path

OUT_DEFAULT = Path(r"C:\SecondBrainStaging\SecondBrain\chats")

SOURCES = {
    "cursor": Path(os.environ.get("APPDATA", "")) / "Cursor" / "User" / "workspaceStorage",
    "vscode-copilot": Path(os.environ.get("APPDATA", "")) / "Code" / "User" / "workspaceStorage",
}


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


def assistant_text(response):
    parts = []
    if isinstance(response, list):
        for item in response:
            if isinstance(item, dict):
                v = item.get("value")
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
    return "\n\n".join(parts).strip()


def convert_file(path: Path, out_dir: Path, label: str):
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return None
    requests = data.get("requests") or []
    lines = []
    for req in requests:
        user = ((req.get("message") or {}).get("text") or "").strip()
        if user:
            lines.append(f"**User**:\n{user}")
        asst = assistant_text(req.get("response"))
        if asst:
            who = "**Cursor**" if label == "cursor" else "**Copilot**"
            lines.append(f"{who}:\n{asst}")
    if not lines:
        return None
    first_user = next((l.split("\n", 1)[-1] for l in lines if l.startswith("**User**")), lines[0])
    title = first_user[:70].replace("\n", " ")
    try:
        date = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        date = "undated"
    header = f"_Source: {label} session {path.stem}_"
    return write_md(out_dir, date, title, [header] + lines)


def run(out_root=OUT_DEFAULT):
    out_root = Path(out_root)
    totals = {}
    for label, ws in SOURCES.items():
        out = out_root / label
        count = 0
        if ws.is_dir():
            for jf in sorted(ws.glob("*/chatSessions/*.json")):
                if convert_file(jf, out, label):
                    count += 1
        totals[label] = (count, out)
        print(f"{label}: converted {count} sessions to {out}")


if __name__ == "__main__":
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT_DEFAULT
    run(out)
