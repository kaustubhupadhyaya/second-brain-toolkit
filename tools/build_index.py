"""Regenerate D:\\SecondBrain\\00_INDEX.md — the entry point agents read first.

Usage: python build_index.py
"""
import datetime
from pathlib import Path

ROOT = Path(r"E:\SecondBrain")
SKIP_DIRS = {"_tools", "_intake"}
EXTS = {".md", ".pdf", ".docx", ".txt", ".json", ".csv"}


def run():
    lines = [
        "# Second Brain — Master Index",
        "",
        f"_Regenerated {datetime.date.today().isoformat()} by build_index.py. "
        "This folder is the single source of truth consolidated from Google Docs, "
        "OneDrive, GitHub, ChatGPT, Claude, Gemini, iCloud, and YouTube._",
        "",
    ]
    for section in sorted(p for p in ROOT.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        files = sorted(f for f in section.rglob("*") if f.is_file() and f.suffix.lower() in EXTS)
        if not files:
            continue
        lines.append(f"## {section.name}/  ({len(files)} files)")
        lines.append("")
        for f in files:
            rel = f.relative_to(ROOT)
            lines.append(f"- {rel.as_posix()}")
        lines.append("")
    (ROOT / "00_INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"index: {sum(1 for l in lines if l.startswith('- '))} files listed in 00_INDEX.md")


if __name__ == "__main__":
    run()
