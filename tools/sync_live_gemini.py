"""Sync live Gemini web conversations to SecondBrain markdown.

Fetches active conversation threads directly from Gemini Web via the local
session credentials in the DPAPI vault, writes individual conversation threads
and daily activity files in E:\\SecondBrain\\chats\\gemini\\ (staging), refreshes
E:\\SecondBrain\\00_INDEX.md, then mirrors the threads into the Drive vault
(G:\\My Drive\\SecondBrain\\30_Resources\\ai-chats) with the second-brain skill.

Designed to be invoked hourly via Windows Task Scheduler (task SecondBrain-Gemini-HourlySync).
Exit code 0 = synced, 1 = failed. After ALERT_AFTER consecutive failures an alert note is written to the vault
inbox so a silent outage is visible; it is marked resolved (not deleted) on the next success.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

# Add facecard repo for authenticated_client
FACECARD_DIR = Path(r"C:\Users\Admin\GitHub\nano-banana-facecard")
if str(FACECARD_DIR) not in sys.path:
    sys.path.insert(0, str(FACECARD_DIR))

from session import authenticated_client, AuthError

# Staging lives on E: (USB SSD) when it is attached; otherwise on C:, so the sync never depends on a removable disk.
# Logs and failure state are ALWAYS on C: so a missing E: can never again make the sync fail without a trace
# (2026-10-21: E: was unplugged, the script crashed on mkdir before logging anything and exited 1 hourly, silently).
STAGE_ROOT = Path(r"E:\SecondBrain") if Path("E:/").exists() else Path(r"C:\SecondBrainStaging\SecondBrain")
OUT_DIR = STAGE_ROOT / "chats" / "gemini"
TOOLS_DIR = STAGE_ROOT / "_tools"
LOGS_DIR = Path(r"C:\SecondBrainStaging\_logs")
LOG_FILE = LOGS_DIR / "gemini_sync.log"
FAIL_STATE = LOGS_DIR / "gemini_sync_failures.json"
ALERT_AFTER = 3
VAULT = Path(r"G:\My Drive\SecondBrain")
ALERT_NOTE = VAULT / "00_Inbox" / "ALERT-gemini-sync-failing.md"
BRAIN = Path(r"C:\Users\Admin\GitHub\agent-config\skills\second-brain\scripts\brain.py")


def record_result(ok: bool, logger, reason: str = "") -> None:
    """Track consecutive failures; raise or resolve the vault alert note."""
    import json
    state = {"consecutive_failures": 0, "last_success": None, "last_failure": None}
    try:
        state.update(json.loads(FAIL_STATE.read_text(encoding="utf-8")))
    except Exception:
        pass
    now = datetime.datetime.now().isoformat(timespec="seconds")
    if ok:
        was_failing = state["consecutive_failures"] >= ALERT_AFTER
        state.update(consecutive_failures=0, last_success=now)
        if was_failing and ALERT_NOTE.exists():
            text = ALERT_NOTE.read_text(encoding="utf-8")
            ALERT_NOTE.write_text(text.rstrip("\n") + f"\n\n**Resolved {now}:** the sync succeeded again.\n",
                                  encoding="utf-8")
    else:
        state.update(consecutive_failures=state["consecutive_failures"] + 1, last_failure=now)
        if state["consecutive_failures"] >= ALERT_AFTER and VAULT.exists():
            today = datetime.date.today().isoformat()
            ALERT_NOTE.parent.mkdir(parents=True, exist_ok=True)
            ALERT_NOTE.write_text(
                "---\n"
                'title: "ALERT: Gemini hourly sync is failing"\n'
                "tags: [alert, gemini, sync]\n"
                f"created: {today}\n"
                f"updated: {today}\n"
                f'summary: "The hourly Gemini chat sync has failed {state["consecutive_failures"]} times in a row; new Gemini chats are not reaching the second brain."\n'
                "filed_by: agent\n"
                "---\n\n"
                "# ALERT: Gemini hourly sync is failing\n\n"
                f"- Consecutive failures: {state['consecutive_failures']} (last {now})\n"
                f"- Last success: {state['last_success'] or 'unknown'}\n"
                f"- Last error: {reason or 'see log'}\n"
                f"- Log: `{LOG_FILE}`\n"
                "- Usual cause: expired Gemini cookies. The facecard keep-alive task renews them; otherwise refresh via\n"
                "  the vault popup (`vault.py gui --request \"gemini-cookies-psid:account,gemini-cookies:account\"`).\n",
                encoding="utf-8")
            logger.error(f"Alert note written: {ALERT_NOTE}")
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    FAIL_STATE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def mirror_to_vault(logger) -> None:
    if not VAULT.exists():
        logger.warning("Vault G: not mounted; skipped the vault mirror (staging on E: is up to date).")
        return
    res = subprocess.run(
        [sys.executable, str(BRAIN), "mirror", "--source", str(OUT_DIR), "--to", "30_Resources/ai-chats",
         "--tags", "ai-chat,gemini", "--label", "Gemini", "--prefix", "gemini_"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if res.returncode == 0:
        logger.info(res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "vault mirror done")
    else:
        raise RuntimeError(f"vault mirror failed: {res.stderr.strip()[-300:]}")


def setup_logging():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Simple size-based rotation: keep up to 5MB
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
        backup = LOG_FILE.with_suffix(".log.bak")
        try:
            if backup.exists():
                backup.unlink()
            LOG_FILE.rename(backup)
        except Exception:
            pass

    logger = logging.getLogger("gemini_sync")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


def slugify(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:maxlen].rstrip("-") or "chat"


def format_turn(turn, turn_num: int) -> str:
    role = "You" if turn.role == "user" else "Gemini"
    body = (turn.text or "").strip()

    media_lines = []
    if hasattr(turn, "model_output") and turn.model_output:
        mo = turn.model_output
        candidates = getattr(mo, "candidates", []) or []
        for cand in candidates:
            for vid in getattr(cand, "videos", []) or []:
                v_title = getattr(vid, "title", "Video") or "Video"
                v_url = getattr(vid, "url", "")
                if v_url:
                    media_lines.append(f"📹 **[{v_title}]({v_url})**")
            for img in getattr(cand, "images", []) or []:
                i_url = getattr(img, "url", "")
                if i_url:
                    media_lines.append(f"🖼️ [Image]({i_url})")

    parts = [f"### Turn {turn_num} — {role}", ""]
    if body:
        parts.append(body)
        parts.append("")
    if media_lines:
        parts.extend(media_lines)
        parts.append("")

    return "\n".join(parts)


async def sync():
    logger = setup_logging()
    logger.info("=" * 60)
    logger.info("Starting Gemini -> SecondBrain chat sync...")

    try:
        async with authenticated_client(verbose=False, auto_refresh=False) as client:
            chats = client.list_chats()
            if not chats:
                logger.info("No active conversations found on Gemini Web.")
                record_result(True, logger)
                return True

            logger.info(f"Connected. Found {len(chats)} conversations on Gemini Web.")
            OUT_DIR.mkdir(parents=True, exist_ok=True)

            synced_threads = 0
            new_threads = 0
            updated_threads = 0
            new_or_updated_days: dict[str, list] = {}

            for i, c in enumerate(chats, 1):
                dt = (
                    datetime.datetime.fromtimestamp(c.timestamp)
                    if c.timestamp
                    else datetime.datetime.now()
                )
                date_str = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M:%S")
                slug = slugify(c.title or "conversation")

                try:
                    history = await client.read_chat(c.cid, limit=100)
                except Exception as e:
                    logger.warning(f"Failed to read chat {c.cid} ('{c.title}'): {e}")
                    continue

                if not history or not history.turns:
                    continue

                turns_chronological = list(reversed(history.turns))
                thread_filename = f"{date_str}_thread_{c.cid}_{slug}.md"
                thread_path = OUT_DIR / thread_filename

                header = [
                    f"# {c.title or 'Gemini Conversation'}",
                    "",
                    f"_Date: {date_str} {time_str}_",
                    f"_Chat ID: `{c.cid}`_",
                    f"_URL: https://gemini.google.com/app/{c.cid}_",
                    f"_Turns: {len(turns_chronological)}_",
                    "",
                    "---",
                    "",
                ]

                turn_blocks = [
                    format_turn(t, idx + 1)
                    for idx, t in enumerate(turns_chronological)
                ]
                new_content = "\n".join(header) + "\n" + "\n---\n\n".join(turn_blocks) + "\n"

                # Check if file already exists with identical content
                is_new = not thread_path.exists()
                is_changed = True
                if not is_new:
                    try:
                        existing_content = thread_path.read_text(encoding="utf-8")
                        if existing_content == new_content:
                            is_changed = False
                    except Exception:
                        is_changed = True

                if is_new or is_changed:
                    thread_path.write_text(new_content, encoding="utf-8")
                    synced_threads += 1
                    if is_new:
                        new_threads += 1
                        logger.info(f"  [NEW] {c.title} ({date_str}, {len(turns_chronological)} turns)")
                    else:
                        updated_threads += 1
                        logger.info(f"  [UPDATED] {c.title} ({date_str}, {len(turns_chronological)} turns)")

                    if date_str not in new_or_updated_days:
                        new_or_updated_days[date_str] = []
                    new_or_updated_days[date_str].append({
                        "time": time_str,
                        "title": c.title,
                        "cid": c.cid,
                        "turns": turns_chronological,
                    })

            # For days with new/updated web chats, maintain the daily markdown summary
            for day, day_chats in new_or_updated_days.items():
                daily_path = OUT_DIR / f"{day}_gemini.md"
                section_lines = [
                    f"\n## Live Web Chat Threads ({day})\n",
                ]
                for dc in day_chats:
                    section_lines.append(f"### {dc['time']} — [{dc['title']}](https://gemini.google.com/app/{dc['cid']})")
                    for t in dc["turns"]:
                        role = "You" if t.role == "user" else "Gemini"
                        snippet = (t.text or "").strip()
                        if len(snippet) > 300:
                            snippet = snippet[:300] + "..."
                        section_lines.append(f"**{role}:** {snippet}\n")
                    section_lines.append("---\n")

                if daily_path.exists():
                    existing = daily_path.read_text(encoding="utf-8")
                    if "Live Web Chat Threads" not in existing:
                        daily_path.write_text(existing + "\n" + "\n".join(section_lines), encoding="utf-8")
                else:
                    body = [
                        f"# Gemini activity {day}",
                        "",
                        f"_{len(day_chats)} threads from Gemini Web live session._",
                        "",
                    ] + section_lines
                    daily_path.write_text("\n".join(body), encoding="utf-8")

            logger.info(
                f"Sync summary: {new_threads} new, {updated_threads} updated, "
                f"{len(chats) - new_threads - updated_threads} unchanged."
            )

            # Rebuild master index if changes were made or to ensure freshness
            index_script = TOOLS_DIR / "build_index.py"
            if index_script.exists() and STAGE_ROOT == Path(r"E:\SecondBrain"):
                logger.info("Updating SecondBrain 00_INDEX.md...")
                try:
                    res = subprocess.run(
                        [sys.executable, str(index_script)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if res.returncode == 0:
                        logger.info(f"Index updated: {res.stdout.strip()}")
                    else:
                        logger.warning(f"build_index warning: {res.stderr.strip()}")
                except Exception as ex:
                    logger.warning(f"Could not run build_index.py: {ex}")

            mirror_to_vault(logger)
            logger.info("Gemini sync completed successfully.")
            record_result(True, logger)
            return True

    except AuthError as ae:
        logger.error(f"Gemini authentication failed: {ae}")
        logger.error("Session cookies may have expired. Refresh via vault GUI.")
        record_result(False, logger, f"authentication failed: {ae}")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error during Gemini sync: {e}")
        record_result(False, logger, f"{type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    sys.exit(0 if asyncio.run(sync()) else 1)
