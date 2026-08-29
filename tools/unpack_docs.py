"""Unpack OneDrive / iCloud download zips into the second brain.

Usage:
  python unpack_docs.py microsoft <zip-or-dir>   -> docs/microsoft/
  python unpack_docs.py pdfs <zip-or-dir>        -> pdfs/
  python unpack_docs.py gdocs <takeout-zip>      -> docs/google-docs/  (off-account Takeout)

Every .docx also gets a sibling .md with its extracted plain text so agents
can read it without a Word parser.
"""
import re, sys, zipfile, shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(r"E:\SecondBrain")
DEST = {"microsoft": ROOT / "docs" / "microsoft",
        "pdfs": ROOT / "pdfs",
        "gdocs": ROOT / "docs" / "google-docs"}
KEEP = {".docx", ".doc", ".pdf", ".txt", ".md", ".rtf", ".pages", ".key", ".numbers",
        ".xlsx", ".csv", ".pptx", ".html", ".odt"}


def docx_to_md(docx: Path):
    try:
        with zipfile.ZipFile(docx) as z:
            xml = z.read("word/document.xml")
    except Exception:
        return
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    root = ET.fromstring(xml)
    paras = []
    for p in root.iter("{%s}p" % ns["w"]):
        text = "".join(t.text or "" for t in p.iter("{%s}t" % ns["w"]))
        paras.append(text)
    text = "\n\n".join(p for p in paras if p.strip())
    if text.strip():
        docx.with_suffix(".md").write_text(f"# {docx.stem}\n\n{text}\n", encoding="utf-8")


def run(kind, src):
    dest = DEST[kind]
    dest.mkdir(parents=True, exist_ok=True)
    src = Path(src)
    count = 0
    if src.suffix.lower() == ".zip":
        with zipfile.ZipFile(src) as z:
            for info in z.infolist():
                ext = Path(info.filename).suffix.lower()
                if info.is_dir() or ext not in KEEP:
                    continue
                safe = re.sub(r'[<>:"|?*]', "_", info.filename)
                target = dest / safe
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as f, target.open("wb") as out:
                    shutil.copyfileobj(f, out)
                count += 1
    else:
        for f in src.rglob("*"):
            if f.is_file() and f.suffix.lower() in KEEP:
                target = dest / f.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                count += 1
    for docx in dest.rglob("*.docx"):
        if not docx.with_suffix(".md").exists():
            docx_to_md(docx)
    print(f"{kind}: {count} files -> {dest} (docx converted to sibling .md)")


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] not in DEST:
        print(__doc__)
    else:
        run(sys.argv[1], sys.argv[2])
