"""Convert Claude conversations to markdown.

Two sources:
  1. Claude Code local transcripts:  ~/.claude/projects/**/*.jsonl
  2. claude.ai data-export zips:     conversations.json inside each zip

Usage:
  python convert_claude.py code                    # local Claude Code transcripts
  python convert_claude.py export <zip-or-dir> <account-label>
"""
import json, re, sys, zipfile
from pathlib import Path

OUT_ROOT = Path(r"E:\SecondBrain\chats")


def slugify(text, maxlen=60):
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


# ---------- Claude Code jsonl ----------

def text_of_content(content):
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p.strip())


def convert_code_jsonl(path: Path, out_dir: Path):
    lines, title, date = [], None, None
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("type") == "summary" and not title:
                title = rec.get("summary")
            if rec.get("type") not in ("user", "assistant"):
                continue
            if rec.get("isSidechain"):
                continue
            msg = rec.get("message") or {}
            text = text_of_content(msg.get("content"))
            if not text.strip():
                continue
            if text.startswith(("<local-command-", "<command-name>", "<system-reminder>", "Caveat:")):
                continue
            role = "**User**" if rec["type"] == "user" else "**Claude**"
            if date is None and rec.get("timestamp"):
                date = rec["timestamp"][:10]
            lines.append(f"{role}:\n{text.strip()}")
    if not lines:
        return None
    if not title:
        first = lines[0].split("\n", 1)[-1]
        title = first[:70].replace("\n", " ")
    return write_md(out_dir, date or "undated", title, lines)


def run_code():
    src = Path.home() / ".claude" / "projects"
    out = OUT_ROOT / "claude-code"
    count = 0
    for jl in src.rglob("*.jsonl"):
        if convert_code_jsonl(jl, out):
            count += 1
    print(f"claude-code: wrote {count} conversations to {out}")


# ---------- claude.ai export ----------

def run_export(src, label):
    src = Path(src)
    out = OUT_ROOT / "claude" / label
    if src.suffix == ".zip":
        with zipfile.ZipFile(src) as z:
            name = next(n for n in z.namelist() if n.endswith("conversations.json"))
            data = json.loads(z.read(name))
    else:
        data = json.loads((src / "conversations.json").read_text(encoding="utf-8"))
    count = 0
    for conv in data:
        title = conv.get("name") or "untitled"
        date = (conv.get("created_at") or "undated")[:10]
        lines = []
        for m in conv.get("chat_messages", []):
            text = m.get("text") or text_of_content(m.get("content"))
            if not text.strip():
                continue
            role = "**User**" if m.get("sender") == "human" else "**Claude**"
            lines.append(f"{role}:\n{text.strip()}")
        if lines:
            write_md(out, date, title, lines)
            count += 1
    print(f"claude.ai [{label}]: wrote {count} conversations to {out}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "code":
        run_code()
    elif len(sys.argv) >= 4 and sys.argv[1] == "export":
        run_export(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
