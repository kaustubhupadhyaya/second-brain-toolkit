#!/usr/bin/env python3
"""Unified Cloud Chat Importer & Vault Promoter for Second Brain.

Ingests export ZIPs/directories for ChatGPT, Claude.ai, and Gemini, converts
conversations into Markdown conforming strictly to AGENTS.md, promotes them
into G:\\My Drive\\SecondBrain\\00_Inbox\\chats\\<service>\\<account>\\, and
updates 00_Inbox\\INDEX.md and 10_Projects\\second-brain-consolidation\\goal-tracker.md.

Mandatory rules:
1. YAML frontmatter on every promoted note.
2. Quoted summary: summary field MUST be enclosed in double quotes.
3. Maximum directory nesting depth <= 3.
4. Auto-update 00_Inbox/INDEX.md in the same operation.
5. NEVER delete source archives.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VAULT_ROOT = Path(r"G:\My Drive\SecondBrain")
INBOX_DIR = VAULT_ROOT / "00_Inbox"
CHATS_DIR = INBOX_DIR / "chats"
INDEX_FILE = INBOX_DIR / "INDEX.md"
GOAL_TRACKER = VAULT_ROOT / "10_Projects" / "second-brain-consolidation" / "goal-tracker.md"
STAGING_INTAKE = Path(r"C:\SecondBrainStaging\intake")

TODAY = datetime.date.today().strftime("%Y-%m-%d")


def slugify(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:maxlen].rstrip("-") or "untitled"


def yaml_quote(val: str) -> str:
    cleaned = val.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{cleaned}"'


def create_frontmatter(
    title: str,
    tags: List[str],
    created: str,
    summary: str,
    extra_fields: Optional[Dict[str, str]] = None,
) -> str:
    tag_str = ", ".join(tags)
    lines = [
        "---",
        f"title: {yaml_quote(title)}",
        f"tags: [{tag_str}]",
        f"created: {created}",
        f"updated: {TODAY}",
        f"summary: {yaml_quote(summary)}",
    ]
    if extra_fields:
        for k, v in extra_fields.items():
            lines.append(f"{k}: {yaml_quote(v)}")
    lines.append("---\n")
    return "\n".join(lines)


def update_inbox_index(promoted_entries: List[Tuple[str, str, str]]):
    """Appends promoted notes to G:\\My Drive\\SecondBrain\\00_Inbox\\INDEX.md.
    promoted_entries: list of (rel_path, title, short_summary)
    """
    if not INDEX_FILE.exists():
        print(f"Warning: {INDEX_FILE} not found. Skipping index update.")
        return

    content = INDEX_FILE.read_text(encoding="utf-8")
    new_section_lines = []

    # Group by service/account
    grouped = defaultdict(list)
    for rel_path, title, short_summary in promoted_entries:
        parts = rel_path.replace("\\", "/").split("/")
        # rel_path is chats/<service>/<account>/<file>.md
        header = "/".join(parts[:3]) if len(parts) >= 3 else "chats"
        grouped[header].append((rel_path, title, short_summary))

    for header, items in sorted(grouped.items()):
        header_tag = f"## {header}/"
        items_to_add = []
        for rel_path, title, short_summary in items:
            link_line = f"- [[{rel_path}]] — {title}"
            if link_line not in content:
                items_to_add.append(link_line)

        if items_to_add:
            if header_tag in content:
                # Insert below header
                idx = content.find(header_tag)
                next_header = content.find("\n## ", idx + len(header_tag))
                if next_header == -1:
                    content = content.rstrip() + "\n" + "\n".join(items_to_add) + "\n"
                else:
                    content = (
                        content[:next_header].rstrip()
                        + "\n"
                        + "\n".join(items_to_add)
                        + "\n\n"
                        + content[next_header:].lstrip()
                    )
            else:
                new_section_lines.append(f"\n{header_tag}\n" + "\n".join(items_to_add) + "\n")

    if new_section_lines:
        content = content.rstrip() + "\n" + "\n".join(new_section_lines)

    INDEX_FILE.write_text(content, encoding="utf-8")
    print(f"Updated {INDEX_FILE} with {len(promoted_entries)} new entries.")


def write_promoted_note(
    dest_dir: Path,
    date: str,
    title: str,
    tags: List[str],
    summary: str,
    body_content: str,
    extra_fields: Optional[Dict[str, str]] = None,
) -> Tuple[Path, str]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(title)
    filename = f"{date}_{slug}.md"
    target_path = dest_dir / filename

    counter = 1
    while target_path.exists():
        counter += 1
        filename = f"{date}_{slug}-{counter}.md"
        target_path = dest_dir / filename

    fm = create_frontmatter(title, tags, date, summary, extra_fields)
    full_text = fm + "\n" + body_content

    target_path.write_text(full_text, encoding="utf-8")
    rel_path = target_path.relative_to(INBOX_DIR).as_posix()
    return target_path, rel_path


# =====================================================================
# 1. CHATGPT PARSER & PROMOTER
# =====================================================================

def _chatgpt_message_text(msg: dict) -> str:
    if not msg:
        return ""
    content = msg.get("content") or {}
    ctype = content.get("content_type")
    if ctype in ("text", "multimodal_text"):
        parts = content.get("parts", [])
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def _chatgpt_ordered_messages(conv: dict) -> list:
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


def promote_chatgpt(src: Path, account_label: str) -> List[Tuple[str, str, str]]:
    """Parses conversations.json from ChatGPT export and writes to SecondBrain."""
    print(f"\n--- Processing ChatGPT Export for [{account_label}] from {src} ---")
    dest_dir = CHATS_DIR / "chatgpt" / account_label

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src, "r") as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            if not name:
                raise ValueError(f"No conversations.json in {src}")
            data = json.loads(z.read(name).decode("utf-8", errors="replace"))
    else:
        conv_file = src / "conversations.json" if src.is_dir() else src
        data = json.loads(conv_file.read_text(encoding="utf-8", errors="replace"))

    promoted = []
    for conv in data:
        title = conv.get("title") or "Untitled Conversation"
        ct = conv.get("create_time")
        date = (
            datetime.datetime.fromtimestamp(ct).strftime("%Y-%m-%d")
            if ct
            else "undated"
        )

        lines = []
        first_user_prompt = ""
        for msg in _chatgpt_ordered_messages(conv):
            role = (msg.get("author") or {}).get("role")
            if role not in ("user", "assistant"):
                continue
            if (msg.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
                continue
            text = _chatgpt_message_text(msg)
            if not text.strip():
                continue
            if role == "user" and not first_user_prompt:
                first_user_prompt = text.strip().replace("\n", " ")[:120]
            who = "**User**" if role == "user" else "**ChatGPT**"
            lines.append(f"{who}:\n{text.strip()}")

        if not lines:
            continue

        short_prompt = first_user_prompt or title
        summary = f"ChatGPT conversation on {title} ({short_prompt})."
        tags = ["chatgpt", "session-transcript", account_label]

        body = f"# {title}\n\n_Date: {date}_\n_Account: {account_label}_\n\n" + "\n\n".join(lines) + "\n"

        target_path, rel_path = write_promoted_note(
            dest_dir=dest_dir,
            date=date,
            title=title,
            tags=tags,
            summary=summary,
            body_content=body,
        )
        promoted.append((rel_path, title, summary))

    print(f"Successfully promoted {len(promoted)} ChatGPT conversations to {dest_dir}")
    return promoted


# =====================================================================
# 2. CLAUDE.AI PARSER & PROMOTER
# =====================================================================

def _claude_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(p for p in parts if p.strip())
    return ""


def promote_claude(src: Path, account_label: str) -> List[Tuple[str, str, str]]:
    """Parses conversations.json from Claude.ai export and writes to SecondBrain."""
    print(f"\n--- Processing Claude.ai Export for [{account_label}] from {src} ---")
    dest_dir = CHATS_DIR / "claude" / account_label

    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src, "r") as z:
            name = next((n for n in z.namelist() if n.endswith("conversations.json")), None)
            if not name:
                raise ValueError(f"No conversations.json in {src}")
            data = json.loads(z.read(name).decode("utf-8", errors="replace"))
    else:
        conv_file = src / "conversations.json" if src.is_dir() else src
        data = json.loads(conv_file.read_text(encoding="utf-8", errors="replace"))

    promoted = []
    for conv in data:
        title = conv.get("name") or "Untitled Conversation"
        created_raw = conv.get("created_at") or ""
        date = created_raw[:10] if created_raw else "undated"

        lines = []
        first_user_prompt = ""
        for m in conv.get("chat_messages", []):
            text = m.get("text") or _claude_content_text(m.get("content"))
            if not text.strip():
                continue
            is_human = m.get("sender") == "human"
            if is_human and not first_user_prompt:
                first_user_prompt = text.strip().replace("\n", " ")[:120]
            role = "**User**" if is_human else "**Claude**"
            lines.append(f"{role}:\n{text.strip()}")

        if not lines:
            continue

        short_prompt = first_user_prompt or title
        summary = f"Claude.ai conversation on {title} ({short_prompt})."
        tags = ["claude", "session-transcript", account_label]

        body = f"# {title}\n\n_Date: {date}_\n_Account: {account_label}_\n\n" + "\n\n".join(lines) + "\n"

        target_path, rel_path = write_promoted_note(
            dest_dir=dest_dir,
            date=date,
            title=title,
            tags=tags,
            summary=summary,
            body_content=body,
        )
        promoted.append((rel_path, title, summary))

    print(f"Successfully promoted {len(promoted)} Claude conversations to {dest_dir}")
    return promoted


# =====================================================================
# 3. GEMINI APPS PARSER & PROMOTER
# =====================================================================

def _parse_gemini_html(content: str) -> list:
    records = []
    cells = re.split(r"outer-cell", content)
    for cell in cells[1:]:
        text = re.sub(r"<br\s*/?>", "\n", cell)
        text = re.sub(r"<[^>]+>", "\n", text)
        text = html.unescape(text)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        prompt, date = None, "undated"
        for i, l in enumerate(lines):
            if l.startswith("Prompted"):
                prompt = (
                    l[len("Prompted") :].strip()
                    or (lines[i + 1] if i + 1 < len(lines) else "")
                )
            m = re.match(r"^[A-Z][a-z]{2} \d{1,2}, \d{4}", l)
            if m:
                try:
                    date = datetime.datetime.strptime(m.group(0), "%b %d, %Y").strftime(
                        "%Y-%m-%d"
                    )
                except ValueError:
                    pass
        if prompt:
            records.append((date, prompt))
    return records


def promote_gemini(src: Path, account_label: str) -> List[Tuple[str, str, str]]:
    """Parses Gemini activity (JSON or HTML) from Google Takeout and promotes to SecondBrain."""
    print(f"\n--- Processing Gemini Export for [{account_label}] from {src} ---")
    dest_dir = CHATS_DIR / "gemini" / account_label

    content, is_json = None, False
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src, "r") as z:
            for n in z.namelist():
                if "Gemini" in n and n.endswith((".json", ".html")):
                    raw = z.read(n).decode("utf-8", errors="replace")
                    is_json = n.endswith(".json")
                    content = raw
                    break
    else:
        for p in src.rglob("*"):
            if "Gemini" in str(p) and p.suffix.lower() in (".json", ".html"):
                content = p.read_text(encoding="utf-8", errors="replace")
                is_json = p.suffix.lower() == ".json"
                break

    if not content:
        print(f"No Gemini activity records found in {src}")
        return []

    records = []
    if is_json:
        data = json.loads(content)
        for item in data:
            title = item.get("title", "")
            date = (item.get("time") or "undated")[:10]
            if title.startswith("Prompted"):
                records.append((date, title[len("Prompted") :].strip()))
    else:
        records = _parse_gemini_html(content)

    by_day = defaultdict(list)
    for date, prompt in records:
        by_day[date].append(prompt)

    promoted = []
    for date, prompts in sorted(by_day.items()):
        title = f"Gemini Activity {date}"
        summary = f"Gemini prompt activity on {date} ({len(prompts)} prompts)."
        tags = ["gemini", "session-transcript", account_label]

        body = (
            f"# {title}\n\n_Date: {date}_\n_Account: {account_label}_\n\n"
            + "\n\n".join(f"**Prompt**: {p}" for p in prompts)
            + "\n"
        )

        target_path, rel_path = write_promoted_note(
            dest_dir=dest_dir,
            date=date,
            title=title,
            tags=tags,
            summary=summary,
            body_content=body,
        )
        promoted.append((rel_path, title, summary))

    print(f"Successfully promoted {len(promoted)} daily Gemini notes to {dest_dir}")
    return promoted


# =====================================================================
# 4. INTAKE BATCH SCANNER
# =====================================================================

def run_intake_sweep():
    """Scans C:\\SecondBrainStaging\\intake\\ for incoming zips and processes them."""
    print(f"Scanning {STAGING_INTAKE} for export archives...")
    if not STAGING_INTAKE.exists():
        print(f"Intake directory does not exist: {STAGING_INTAKE}")
        return

    all_promoted = []
    for zpath in STAGING_INTAKE.glob("*.zip"):
        fname = zpath.name.lower()
        print(f"\nFound archive in intake: {zpath.name}")

        account = "general"
        for candidate in ["google-1", "google-2", "google-3", "google-4", "google-5", "homies", "riskspan", "yt-owner"]:
            if candidate in fname:
                account = candidate
                break

        try:
            if "chatgpt" in fname:
                promoted = promote_chatgpt(zpath, account)
                all_promoted.extend(promoted)
            elif "claude" in fname:
                promoted = promote_claude(zpath, account)
                all_promoted.extend(promoted)
            elif "gemini" in fname or "takeout" in fname:
                promoted = promote_gemini(zpath, account)
                all_promoted.extend(promoted)
            else:
                # Inspect inside zip to determine format
                with zipfile.ZipFile(zpath, "r") as z:
                    names = z.namelist()
                    if any("conversations.json" in n for n in names):
                        sample = json.loads(z.read(next(n for n in names if "conversations.json" in n))[:5000].decode("utf-8", errors="replace") + "[]}")
                        # Check keys
                        if "chat_messages" in str(names) or "sender" in str(sample):
                            promoted = promote_claude(zpath, account)
                        else:
                            promoted = promote_chatgpt(zpath, account)
                        all_promoted.extend(promoted)
                    elif any("Gemini" in n for n in names):
                        promoted = promote_gemini(zpath, account)
                        all_promoted.extend(promoted)
                    else:
                        print(f"Unrecognized archive structure in {zpath.name}. Skipping.")
        except Exception as e:
            print(f"Error processing {zpath.name}: {e}")

    if all_promoted:
        update_inbox_index(all_promoted)
        print(f"\nAll {len(all_promoted)} notes promoted and indexed successfully.")
    else:
        print("No new archives processed.")


# =====================================================================
# CLI INTERFACE
# =====================================================================

def main():
    parser = argparse.ArgumentParser(description="Promote cloud chat exports to Second Brain vault.")
    subparsers = parser.add_subparsers(dest="command")

    # chatgpt
    cg_parser = subparsers.add_parser("chatgpt", help="Process ChatGPT export")
    cg_parser.add_argument("src", type=str, help="Path to ZIP or folder containing conversations.json")
    cg_parser.add_argument("account", type=str, help="Account label (e.g. google-1, google-2, google-5)")

    # claude
    cl_parser = subparsers.add_parser("claude", help="Process Claude export")
    cl_parser.add_argument("src", type=str, help="Path to ZIP or folder containing conversations.json")
    cl_parser.add_argument("account", type=str, help="Account label (e.g. google-1, google-2, homies)")

    # gemini
    gem_parser = subparsers.add_parser("gemini", help="Process Gemini Takeout export")
    gem_parser.add_argument("src", type=str, help="Path to ZIP or folder containing Gemini Takeout")
    gem_parser.add_argument("account", type=str, help="Account label (e.g. google-1, google-2)")

    # sweep
    subparsers.add_parser("sweep", help="Scan C:\\SecondBrainStaging\\intake\\ and process all ZIPs")

    args = parser.parse_args()

    if args.command == "chatgpt":
        promoted = promote_chatgpt(Path(args.src), args.account)
        if promoted:
            update_inbox_index(promoted)
    elif args.command == "claude":
        promoted = promote_claude(Path(args.src), args.account)
        if promoted:
            update_inbox_index(promoted)
    elif args.command == "gemini":
        promoted = promote_gemini(Path(args.src), args.account)
        if promoted:
            update_inbox_index(promoted)
    elif args.command == "sweep":
        run_intake_sweep()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
