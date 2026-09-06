"""Convert Codex CLI/Desktop local sessions to markdown.

Source:  ~/.codex/sessions/**/*.jsonl  (Codex CLI + Codex Desktop/VSCode)
Schema (observed 2026-05 rollout file):
  {"type":"session_meta","payload":{"id","timestamp","cwd","originator",...}}
  {"type":"response_item","payload":{"type":"message","role":"user"|"assistant",
    "content":[{"type":"input_text"|"output_text"|"text","text":...}]}}
  {"type":"event_msg","payload":{"type":"user_message"|"agent_message","message":...}}
  (plus task_started/task_complete/token_count noise — ignored)

response_item is canonical; event_msg is a fallback for sessions where
response_item is missing. Identical consecutive duplicates are dropped
(the store often logs the same text in both streams).

Usage:
  python convert_codex.py [--out DIR]
Writes one .md per session to <out>/codex/.
Same write_md/slugify pattern as convert_claude.py.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

OUT_DEFAULT = Path(r"C:\SecondBrainStaging\SecondBrain\chats\codex")

SKIP_PREFIXES = ("<local-command-", "<command-name>", "<system-reminder>", "Caveat:")


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


def content_text(content):
    parts = []
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                if block.get("type") in ("input_text", "output_text", "text"):
                    parts.append(block.get("text", ""))
    return "\n".join(p for p in parts if p and p.strip())


def convert_session(path: Path, out_dir: Path):
    meta_id, meta_ts, cwd = path.stem, None, None
    ordered = []  # (role, text)
    with path.open(encoding="utf-8", errors="replace") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            rtype = rec.get("type")
            payload = rec.get("payload") or {}
            if rtype == "session_meta":
                meta_id = payload.get("id", meta_id)
                meta_ts = payload.get("timestamp", meta_ts)
                cwd = payload.get("cwd", cwd)
            elif rtype == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                text = content_text(payload.get("content")).strip()
                if not text or text.startswith(SKIP_PREFIXES):
                    continue
                who = "**User**" if role == "user" else "**Codex**"
                ordered.append((who, text))
            elif rtype == "event_msg" and payload.get("type") in ("user_message", "agent_message"):
                # fallback stream — only used if response_item stream is empty
                text = (payload.get("message") or "").strip()
                if not text or text.startswith(SKIP_PREFIXES):
                    continue
                who = "**User**" if payload["type"] == "user_message" else "**Codex**"
                ordered.append((who + ":fallback", text))

    if not ordered:
        return None
    # If both streams present, response_item entries (no :fallback suffix)
    # win; drop fallback dupes of text already seen.
    seen_texts = {t for w, t in ordered if not w.endswith(":fallback")}
    if seen_texts:
        ordered = [(w, t) for w, t in ordered if not w.endswith(":fallback")]
    else:
        ordered = [(w.replace(":fallback", ""), t) for w, t in ordered]
    # Drop exact consecutive duplicates.
    deduped = []
    for who, text in ordered:
        if deduped and deduped[-1] == (who, text):
            continue
        deduped.append((who, text))

    lines = [f"{who}:\n{text}" for who, text in deduped]
    first_user = next((t for w, t in deduped if w == "**User**"), deduped[0][1])
    title = first_user[:70].replace("\n", " ")
    date = (meta_ts or "")[:10] or "undated"
    header = f"_Source: codex session {meta_id}" + (f" | cwd: {cwd}" if cwd else "") + "_"
    return write_md(out_dir, date, title, [header] + lines)


def run(out_root=OUT_DEFAULT):
    src = Path.home() / ".codex" / "sessions"
    out = Path(out_root) / "codex" if Path(out_root).name != "codex" else Path(out_root)
    count = 0
    for jl in sorted(src.rglob("*.jsonl")):
        if convert_session(jl, out):
            count += 1
    print(f"codex: converted {count} sessions to {out}")


if __name__ == "__main__":
    out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT_DEFAULT
    run(out)
