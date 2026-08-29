"""Convert a ChatGPT data-export (zip or unpacked dir) to markdown.

Usage: python convert_chatgpt.py <zip-or-dir> <account-label>
Reads conversations.json, writes one .md per conversation to
D:\\SecondBrain\\chats\\chatgpt\\<account-label>\\
"""
import json, sys, zipfile, datetime
from pathlib import Path
from convert_claude import slugify, write_md

OUT_ROOT = Path(r"E:\SecondBrain\chats\chatgpt")


def load(src):
    src = Path(src)
    if src.suffix == ".zip":
        with zipfile.ZipFile(src) as z:
            name = next(n for n in z.namelist() if n.endswith("conversations.json"))
            return json.loads(z.read(name))
    return json.loads((src / "conversations.json").read_text(encoding="utf-8"))


def message_text(msg):
    if not msg:
        return ""
    content = msg.get("content") or {}
    ctype = content.get("content_type")
    if ctype == "text":
        return "\n".join(p for p in content.get("parts", []) if isinstance(p, str))
    if ctype == "multimodal_text":
        return "\n".join(p for p in content.get("parts", []) if isinstance(p, str))
    return ""


def ordered_messages(conv):
    """Walk the node tree from current_node back to root (the canonical branch)."""
    mapping = conv.get("mapping") or {}
    node_id = conv.get("current_node")
    chain = []
    seen = set()
    while node_id and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if node.get("message"):
            chain.append(node["message"])
        node_id = node.get("parent")
    return list(reversed(chain))


def run(src, label):
    out = OUT_ROOT / label
    count = 0
    for conv in load(src):
        title = conv.get("title") or "untitled"
        ct = conv.get("create_time")
        date = datetime.datetime.fromtimestamp(ct).strftime("%Y-%m-%d") if ct else "undated"
        lines = []
        for msg in ordered_messages(conv):
            role = (msg.get("author") or {}).get("role")
            if role not in ("user", "assistant"):
                continue
            if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
                continue
            text = message_text(msg)
            if not text.strip():
                continue
            who = "**User**" if role == "user" else "**ChatGPT**"
            lines.append(f"{who}:\n{text.strip()}")
        if lines:
            write_md(out, date, title, lines)
            count += 1
    print(f"chatgpt [{label}]: wrote {count} conversations to {out}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
    else:
        run(sys.argv[1], sys.argv[2])
