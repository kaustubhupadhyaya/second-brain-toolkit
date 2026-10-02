"""Convert Google Takeout 'Gemini Apps' + 'AI Mode' activity to markdown.

Usage: python convert_gemini.py <takeout-zip-or-dir>
Looks for 'My Activity/Gemini Apps/MyActivity.html' and
'My Activity/AI Mode/MyActivity.html' (or .json) and writes one markdown
file per day of activity to E:\\SecondBrain\\chats\\gemini\\

Each entry keeps the timestamp, the user's prompt, AND Gemini's full
response text (Takeout embeds both in the activity HTML; formatting is
flattened to plain text, fidelity is best-effort).
"""
import datetime
import json
import re
import sys
import zipfile
import html as htmlmod
from pathlib import Path
from collections import defaultdict

OUT = Path(r"E:\SecondBrain\chats\gemini")

DATE_RE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}:\d{2}\s+[AP]M)"
)


def cell_lines(cell):
    text = re.sub(r"<br\s*/?>", "\n", cell)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = htmlmod.unescape(text)
    return [l.strip() for l in text.split("\n") if l.strip()]


def normalise_ws(s):
    return re.sub(r"\s+", " ", s).strip()


def parse_record(lines):
    """Return dict(product, kind, prompt, dt, response) or None."""
    lines = [l for l in lines if not l.startswith("mdl-cell")]
    if len(lines) < 2:
        return None
    product = lines[0]
    action = lines[1]

    prompt, kind = "", "other"
    if action.startswith("Prompted"):
        kind = "prompt"
        prompt = normalise_ws(action[len("Prompted"):])
    elif action.startswith("Searched for"):
        kind = "search"
        prompt = normalise_ws(action[len("Searched for"):])
    elif action.startswith("Created"):
        kind = "canvas"
        prompt = normalise_ws(action)
    elif action.startswith("Gave feedback"):
        kind = "feedback"
        prompt = normalise_ws(action)
    elif action.startswith("Used"):
        kind = "used"
        prompt = normalise_ws(action)
    elif action.startswith("Searched with"):
        kind = "lens"
        prompt = normalise_ws(action)

    idx = 2
    # prompt may spill onto the next line(s)
    if kind in ("prompt", "search") and not prompt:
        if idx < len(lines):
            prompt = normalise_ws(lines[idx])
            idx += 1
    # AI Mode cells: "Your prompt:" <query> ... "Search's response:" <body>
    body_start = None
    for i, l in enumerate(lines):
        if l.startswith("Your prompt:"):
            kind = "search"
            prompt = normalise_ws(l[len("Your prompt:"):])
            if not prompt and i + 1 < len(lines):
                prompt = normalise_ws(lines[i + 1])
        if l.startswith("Search's response:"):
            body_start = i + 1

    dt, date_idx = None, None
    for i, l in enumerate(lines):
        m = DATE_RE.search(normalise_ws(l))
        if m:
            try:
                dt = datetime.datetime.strptime(m.group(1), "%b %d, %Y, %I:%M:%S %p")
            except ValueError:
                dt = None
            date_idx = i
            break

    response = []
    if date_idx is not None:
        start = max(date_idx + 1, body_start or 0)
        for l in lines[start:]:
            if l.startswith("Why is this here?"):
                break
            response.append(l)
    # drop settings-footer crumbs
    while response and response[-1] in ("here", ".", "Products:", product):
        response.pop()

    if not prompt and not response and dt is None:
        return None
    return {
        "product": product,
        "kind": kind,
        "prompt": prompt,
        "dt": dt,
        "response": response,
    }


def parse_html(content, source):
    records = []
    for cell in re.split(r"outer-cell", content)[1:]:
        rec = parse_record(cell_lines(cell))
        if rec:
            rec["source"] = source
            records.append(rec)
    return records


def find_activity_files(src):
    """Return list of (label, text)."""
    src = Path(src)
    found = []
    if src.suffix == ".zip":
        with zipfile.ZipFile(src) as z:
            for n in z.namelist():
                low = n.lower()
                if "myactivity" in low and n.endswith((".html", ".json")) and (
                    "gemini" in low or "ai mode" in low or "ai_mode" in low
                ):
                    found.append((n, z.read(n).decode("utf-8", errors="replace")))
        return found
    for p in src.rglob("*"):
        if p.suffix.lower() not in (".html", ".json"):
            continue
        low = str(p).lower()
        if "myactivity" not in low:
            continue
        if "gemini" in low or "ai mode" in low or "ai_mode" in low:
            found.append((str(p), p.read_text(encoding="utf-8", errors="replace")))
    return found


def render_entry(rec):
    ts = rec["dt"].strftime("%H:%M") if rec["dt"] else "??:??"
    head = rec["prompt"][:70] if rec["prompt"] else rec["kind"]
    parts = [f"## {ts} — {head}", "", f"_source: {rec['source']} · {rec['product']} · {rec['kind']}_", ""]
    if rec["prompt"]:
        parts += ["**You:**", "", rec["prompt"], ""]
    if rec["response"]:
        parts += ["**Gemini:**", "", "\n".join(rec["response"]), ""]
    return "\n".join(parts).rstrip() + "\n"


def run(src):
    files = find_activity_files(src)
    if not files:
        print("No Gemini/AI Mode activity file found in", src)
        return
    records = []
    for name, content in files:
        label = Path(name).parent.name + "/" + Path(name).name
        if name.endswith(".json"):
            data = json.loads(content)
            for item in data:
                title = item.get("title", "")
                if title.startswith(("Prompted", "Searched")):
                    try:
                        dt = datetime.datetime.fromisoformat((item.get("time") or "")[:19])
                    except ValueError:
                        dt = None
                    records.append({
                        "product": label, "kind": "prompt",
                        "prompt": title.split(" ", 1)[-1], "dt": dt,
                        "response": [], "source": label,
                    })
        else:
            records.extend(parse_html(content, label))

    by_day = defaultdict(list)
    undated = 0
    for rec in records:
        if rec["dt"] is None:
            undated += 1
            continue
        by_day[rec["dt"].date().isoformat()].append(rec)

    OUT.mkdir(parents=True, exist_ok=True)
    for day in sorted(by_day):
        entries = sorted(by_day[day], key=lambda r: r["dt"])
        body = [f"# Gemini activity {day}", "",
                f"_{len(entries)} entries from Google Takeout (Gemini Apps + AI Mode)._", ""]
        for rec in entries:
            body.append(render_entry(rec))
            body.append("---")
            body.append("")
        (OUT / f"{day}_gemini.md").write_text("\n".join(body), encoding="utf-8")

    dts = [r["dt"] for r in records if r["dt"]]
    span = f"{min(dts).date()} .. {max(dts).date()}" if dts else "n/a"
    print(f"gemini: {len(records)} records ({len(by_day)} days, span {span}, {undated} undated) -> {OUT}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    else:
        run(sys.argv[1])
