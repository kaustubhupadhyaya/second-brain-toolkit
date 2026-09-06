"""One-time promotion: copy the already-converted Claude Code transcripts
and repo code-notes into G:\\My Drive\\SecondBrain\\00_Inbox, per the vault's
own AGENTS.md contract (new captures land in Inbox; only Kaustubh promotes
them onward). Adds valid YAML frontmatter to each file and regenerates
00_Inbox/INDEX.md.

Usage: python promote_to_vault.py
"""
import re
from datetime import datetime
from pathlib import Path

SRC_CHATS = Path(r"C:\SecondBrainStaging\SecondBrain\chats\claude-code")
SRC_NOTES = Path(r"C:\SecondBrainStaging\SecondBrain\code-notes")
VAULT_INBOX = Path(r"G:\My Drive\SecondBrain\00_Inbox")
DEST_CHATS = VAULT_INBOX / "claude-code-sessions"
DEST_NOTES = VAULT_INBOX / "code-notes"

DATE_TITLE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)\.md$")


def yaml_str(s: str) -> str:
    """Quote a YAML scalar safely - the vault's own documented gotcha is an
    unquoted ': ' silently invalidating the whole frontmatter block."""
    s = s.replace('"', "'").replace("\n", " ").strip()
    return f'"{s}"'


def frontmatter(title, tags, created, updated, summary) -> str:
    tag_list = ", ".join(tags)
    return (
        "---\n"
        f"title: {yaml_str(title)}\n"
        f"tags: [{tag_list}]\n"
        f"created: {created}\n"
        f"updated: {updated}\n"
        f"summary: {yaml_str(summary)}\n"
        "---\n\n"
    )


def promote_chats():
    DEST_CHATS.mkdir(parents=True, exist_ok=True)
    entries = []
    for f in sorted(SRC_CHATS.glob("*.md")):
        m = DATE_TITLE_RE.match(f.name)
        date = m.group(1) if m else datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
        slug = m.group(2) if m else f.stem
        title_line = f.read_text(encoding="utf-8").split("\n", 1)[0].lstrip("# ").strip()
        title = title_line or slug.replace("-", " ")
        body = f.read_text(encoding="utf-8")
        fm = frontmatter(
            title=title,
            tags=["claude-code", "session-transcript"],
            created=date, updated=date,
            summary=f"Claude Code session transcript from {date}: {title[:100]}",
        )
        dest = DEST_CHATS / f.name
        dest.write_text(fm + body, encoding="utf-8")
        entries.append((f.name, title))
    return entries


def has_frontmatter(text: str) -> bool:
    """Line-based check (not a char-count window - SKILL.md files can have
    long YAML block-scalar descriptions that blow past any small window)."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return False
    return any(l.strip() == "---" for l in lines[1:200])


def promote_notes():
    DEST_NOTES.mkdir(parents=True, exist_ok=True)
    entries = []
    for f in sorted(SRC_NOTES.rglob("*.md")):
        rel = f.relative_to(SRC_NOTES)
        repo = rel.parts[0]
        flat_name = f"{repo}--{'-'.join(rel.parts[1:])}" if len(rel.parts) > 1 else f"{repo}--{rel.name}"
        flat_name = flat_name.replace(" ", "-")
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d")
        body = f.read_text(encoding="utf-8", errors="replace")
        title = f"{repo}: {rel.as_posix()}"
        dest = DEST_NOTES / flat_name
        if has_frontmatter(body):
            # Already has its own (often richer) frontmatter - preserve as-is,
            # per the vault's own rule 4 ("preserve frontmatter"). Don't
            # double-stack a second block on top of it.
            dest.write_text(body, encoding="utf-8")
        else:
            fm = frontmatter(
                title=title,
                tags=["code-notes", "github", repo.lower().replace("_", "-")],
                created=mtime, updated=mtime,
                summary=f"Note extracted from the {repo} GitHub repo, originally at {rel.as_posix()}.",
            )
            dest.write_text(fm + body, encoding="utf-8")
        entries.append((flat_name, title))
    return entries


def update_index(chat_entries, note_entries):
    lines = ["# 00_Inbox — Unprocessed Captures", "",
             "_Unprocessed. Kaustubh promotes items from here into Projects/Areas/Resources._", ""]
    lines.append("## claude-code-sessions/")
    for name, title in chat_entries:
        lines.append(f"- [[claude-code-sessions/{name}]] — {title}")
    lines.append("")
    lines.append("## code-notes/")
    for name, title in note_entries:
        lines.append(f"- [[code-notes/{name}]] — {title}")
    lines.append("")
    existing = VAULT_INBOX / "INDEX.md"
    old = existing.read_text(encoding="utf-8") if existing.exists() else ""
    for line in old.splitlines():
        if line.startswith("- [[") and "claude-code-sessions/" not in line and "code-notes/" not in line:
            lines.append(line)
    existing.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    chats = promote_chats()
    notes = promote_notes()
    update_index(chats, notes)
    print(f"Promoted {len(chats)} claude-code sessions + {len(notes)} code-notes into {VAULT_INBOX}")
