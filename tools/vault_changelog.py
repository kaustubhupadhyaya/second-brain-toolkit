"""Nightly change log for the second-brain vault (task SecondBrain-ChangeLog, 23:50).

Compares the vault (G:\\My Drive\\SecondBrain) with the snapshot from the previous run and adds one line per real
change (note added, edited, moved or deleted) to 99_System/vault-change-history.md under "Daily log (automatic)".
A rewrite that leaves a note's text the same is not a change, and neither is a bumped "updated:" line. Folder
INDEX.md files, dot folders (.obsidian) and the history note itself are not tracked.

The previous text of every edited or deleted note is kept in C:\\SecondBrainStaging\\_changelog\\versions\\<date>\\,
so any edit can be undone by copying that file back.

The first run (no snapshot yet) only records the baseline. Times are IST (this PC's clock).
Exit codes: 0 ok, 1 refused or failed (nothing recorded, the next run picks the changes up), 2 vault not mounted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

VAULT = Path(os.environ.get("SECOND_BRAIN", r"G:\My Drive\SecondBrain"))
HISTORY = VAULT / "99_System" / "vault-change-history.md"
MARKER = "<!-- vault_changelog.py adds day sections below this line -->"
STATE_ROOT = Path(r"C:\SecondBrainStaging\_changelog")
SNAP = STATE_ROOT / "snapshot"
VERSIONS = STATE_ROOT / "versions"
STATE = STATE_ROOT / "state.json"
LOG_FILE = Path(r"C:\SecondBrainStaging\_logs\vault_changelog.log")
TEXT_EXT = {".md", ".markdown", ".txt", ".csv", ".json", ".yaml", ".yml"}
SKIP_NAMES = {"INDEX.md", "desktop.ini"}
SKIP_SUFFIXES = (".part", ".tmp", "~")
GROUP_AT = 5  # more changes than this of one kind in one folder become a single line
MIN_KEEP = 0.5  # refuse to record if fewer than half the known files are visible (Drive offline, G: half-mounted)

FM_RE = re.compile(r"\A---\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
DAY_RE = re.compile(r"(?m)^### (\d{4}-\d{2}-\d{2})\s*$")


def log(msg: str) -> None:
    line = f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def tracked(path: Path) -> bool:
    if path == HISTORY or path.name in SKIP_NAMES or path.name.startswith("."):
        return False
    return not path.name.endswith(SKIP_SUFFIXES)


def walk(root: Path):
    for entry in os.scandir(root):
        if entry.name.startswith("."):
            continue  # .obsidian, Drive's .tmp.drive* folders
        p = Path(entry.path)
        if entry.is_dir(follow_symlinks=False):
            yield from walk(p)
        elif tracked(p):
            yield p


def is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXT


def normalize(raw: bytes) -> str:
    """Text as compared: LF line ends, no frontmatter 'updated:' line, no trailing blank lines."""
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    m = FM_RE.match(text)
    if m:
        fm = "\n".join(l for l in m.group(1).split("\n") if not l.startswith("updated:"))
        text = f"---\n{fm}\n---\n" + text[m.end():]
    return text.rstrip() + "\n"


def title_of(text: str, path: Path) -> str:
    m = FM_RE.match(text)
    if m:
        t = re.search(r"(?m)^title:[ \t]*(.+)$", m.group(1))
        if t:
            return t.group(1).strip().strip("\"'")
    h = re.search(r"(?m)^#\s+(.+)$", text)
    return h.group(1).strip() if h else path.stem


def short(text: str, words: int = 6) -> str:
    """First words of a line as plain text: link targets, list markers and Markdown symbols dropped."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](target) -> label
    text = re.sub(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"^\s*(?:[-*+>]|\d+\.)\s+", "", text)
    w = re.sub(r"[#*`|]+", " ", text).split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def rel(path: Path) -> str:
    return path.relative_to(VAULT).as_posix()


def folder_of(r: str) -> str:
    return r.rsplit("/", 1)[0] if "/" in r else "vault root"


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def scan(prev: dict) -> tuple[dict, dict, set]:
    """Return (state, raw text by path, paths that exist but could not be read this run)."""
    state, texts, unreadable = {}, {}, set()
    for p in walk(VAULT):
        r = rel(p)
        try:
            raw = read_bytes(p)
            st = p.stat()
        except OSError:
            unreadable.add(r)
            if r in prev:
                state[r] = prev[r]
            continue
        if is_text(p):
            norm = normalize(raw)
            h = hashlib.sha256(norm.encode("utf-8")).hexdigest()
            title = title_of(norm, p)
            texts[r] = raw
        else:
            h = hashlib.sha256(raw).hexdigest()
            title = p.name
        state[r] = {"h": h, "size": st.st_size, "mtime": st.st_mtime, "title": title}
    return state, texts, unreadable


def snap_text(r: str) -> str | None:
    p = SNAP / r
    try:
        return normalize(p.read_bytes())
    except OSError:
        return None


def describe_edit(r: str, old: str | None, new_raw: bytes, title: str) -> str:
    t = f'"{short(title, 7)}"'
    if old is None:
        return f"Edited {t} in {folder_of(r)}"
    new = normalize(new_raw)
    a, b = old.split("\n"), new.split("\n")
    added, removed, first_new = [], [], None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += [l for l in a[i1:i2] if l.strip()]
        if tag in ("replace", "insert"):
            added += [l for l in b[j1:j2] if l.strip()]
            if first_new is None:
                first_new = j1
    section = None
    if first_new is not None:
        for l in reversed(b[: first_new + 1]):
            if l.startswith("#"):
                section = l.lstrip("#").strip()
                break
    counts = f"(+{len(added)}/-{len(removed)} lines)"
    where = f' under "{short(section, 5)}"' if section and section != title else ""
    if added and not removed:
        return f'Added to {t}{where}: "{short(added[0])}" {counts}'
    if removed and not added:
        return f'Removed from {t}{where}: "{short(removed[0])}" {counts}'
    if added:
        return f'Edited {t}{where}: "{short(added[0])}" {counts}'
    return f"Edited {t} (whitespace only)"


def when(entry: dict | None, last_run: float, now: dt.datetime) -> dt.datetime:
    """A file's modified time when it falls after the last run; otherwise (copies keep old times) the run time."""
    if entry and last_run < entry["mtime"] <= now.timestamp() + 60:
        return dt.datetime.fromtimestamp(entry["mtime"])
    return now


def detect(prev: dict, cur: dict, texts: dict, last_run: float, now: dt.datetime) -> list[dict]:
    gone = {r for r in prev if r not in cur}
    new = {r for r in cur if r not in prev}
    changes = []
    # moves: same file name in a new folder, else identical content under a new name
    by_name_gone: dict[str, list[str]] = {}
    for r in gone:
        by_name_gone.setdefault(r.rsplit("/", 1)[-1], []).append(r)
    by_hash_gone = {prev[r]["h"]: r for r in gone}
    for r in sorted(new):
        name = r.rsplit("/", 1)[-1]
        src = None
        if len(by_name_gone.get(name, [])) == 1:
            src = by_name_gone.pop(name)[0]
        elif cur[r]["h"] in by_hash_gone and by_hash_gone[cur[r]["h"]] in gone:
            src = by_hash_gone[cur[r]["h"]]
        if src:
            gone.discard(src)
            changes.append({"kind": "moved", "path": r, "from": src, "t": now, "old": prev[src],
                            "text": f'Moved "{short(cur[r]["title"], 7)}" from {folder_of(src)} to {folder_of(r)}'})
        else:
            noun = "note" if r.endswith(".md") else "file"
            changes.append({"kind": "added", "path": r, "t": when(cur[r], last_run, now),
                            "text": f'Added {noun} "{short(cur[r]["title"], 7)}" in {folder_of(r)}'})
    for r in sorted(gone):
        noun = "note" if r.endswith(".md") else "file"
        changes.append({"kind": "deleted", "path": r, "t": now, "old": prev[r],
                        "text": f'Deleted {noun} "{short(prev[r]["title"], 7)}" from {folder_of(r)}'})
    for r in sorted(cur):
        if r in prev and r not in new and prev[r]["h"] != cur[r]["h"]:
            text = (describe_edit(r, snap_text(r), texts[r], cur[r]["title"]) if r in texts
                    else f'Replaced file "{cur[r]["title"]}" in {folder_of(r)}')
            changes.append({"kind": "edited", "path": r, "t": when(cur[r], last_run, now), "text": text})
    return changes


def group(changes: list[dict]) -> list[tuple[dt.datetime, str]]:
    """One line per change, except a folder with more than GROUP_AT changes of one kind on one day."""
    buckets: dict[tuple, list[dict]] = {}
    for c in changes:
        buckets.setdefault((c["t"].date(), c["kind"], folder_of(c["path"])), []).append(c)
    lines = []
    for (day, kind, folder), items in buckets.items():
        if len(items) <= GROUP_AT:
            lines += [(c["t"], c["text"]) for c in items]
            continue
        names = [Path(c["path"]).stem for c in sorted(items, key=lambda c: c["path"])]
        more = f" and {len(names) - 4} more" if len(names) > 4 else ""
        lines.append((max(c["t"] for c in items),
                      f"{kind.capitalize()} {len(items)} notes in {folder}: {', '.join(names[:4])}{more}"))
    return sorted(lines)


def write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(text, encoding="utf-8", newline="")
    os.replace(tmp, path)


def append_history(lines: list[tuple[dt.datetime, str]]) -> None:
    raw = HISTORY.read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    if MARKER not in text:
        raise RuntimeError(f"marker line missing in {HISTORY}")
    head, tail = text.split(MARKER, 1)
    days: dict[str, list[str]] = {}
    parts = DAY_RE.split(tail)
    for i in range(1, len(parts), 2):
        days[parts[i]] = [l for l in parts[i + 1].split("\n") if l.strip()]
    for t, line in lines:
        days.setdefault(t.strftime("%Y-%m-%d"), []).append(f"- {t:%H:%M} {line}")
    body = "".join(f"\n\n### {d}\n\n" + "\n".join(sorted(days[d], key=lambda l: l[2:7])) for d in sorted(days))
    head = re.sub(r"(?m)^updated:.*$", f"updated: {dt.date.today().isoformat()}", head, count=1)
    out = head + MARKER + body + "\n"
    write_atomic(HISTORY, out.replace("\n", "\r\n") if crlf else out)


def keep_version(r: str, now: dt.datetime) -> None:
    src = SNAP / r
    if not src.exists():
        return
    dest = VERSIONS / now.strftime("%Y-%m-%d") / r
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}.{now:%H%M%S}{dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def update_snapshot(changes: list[dict], cur: dict, texts: dict, now: dt.datetime, baseline: bool) -> None:
    if baseline:
        for r, raw in texts.items():
            p = SNAP / r
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        return
    for c in changes:
        r = c["path"]
        if c["kind"] in ("edited", "deleted"):
            keep_version(r, now)
        if c["kind"] == "moved":
            old = SNAP / c["from"]
            if old.exists():
                old.unlink()
        if c["kind"] == "deleted":
            (SNAP / r).unlink(missing_ok=True)
        elif r in texts:
            p = SNAP / r
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(texts[r])


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the lines that would be added; write nothing")
    a = ap.parse_args()

    if not (VAULT / "AGENTS.md").exists():
        log(f"vault not mounted at {VAULT}; nothing recorded")
        return 2
    now = dt.datetime.now().replace(microsecond=0)
    saved = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else None
    prev = saved["files"] if saved else {}
    last_run = saved["last_run"] if saved else 0.0

    cur, texts, unreadable = scan(prev)
    if unreadable:
        log(f"{len(unreadable)} file(s) unreadable this run, kept as before: {', '.join(sorted(unreadable)[:5])}")
    if prev and len(cur) < MIN_KEEP * len(prev):
        log(f"refused: only {len(cur)} of {len(prev)} known files visible; nothing recorded")
        return 1

    if saved is None:
        if a.dry_run:
            log(f"dry run: no snapshot yet; a real run would record a baseline of {len(cur)} files")
            return 0
        update_snapshot([], cur, texts, now, baseline=True)
        STATE.write_text(json.dumps({"last_run": now.timestamp(), "files": cur}), encoding="utf-8")
        log(f"baseline recorded: {len(cur)} files ({len(texts)} text snapshots)")
        return 0

    changes = detect(prev, cur, texts, last_run, now)
    lines = group(changes)
    if a.dry_run:
        for t, line in lines:
            print(f"{t:%Y-%m-%d %H:%M} {line}")
        log(f"dry run: {len(lines)} line(s) from {len(changes)} change(s); nothing written")
        return 0
    if lines:
        try:
            append_history(lines)
        except (OSError, RuntimeError) as e:
            log(f"failed to update {HISTORY.name}: {e}; nothing recorded")
            return 1
    update_snapshot(changes, cur, texts, now, baseline=False)
    STATE.write_text(json.dumps({"last_run": now.timestamp(), "files": cur}), encoding="utf-8")
    log(f"{len(changes)} change(s), {len(lines)} line(s) added to {rel(HISTORY)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
