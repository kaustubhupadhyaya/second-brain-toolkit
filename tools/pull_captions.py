"""Pull captions/transcripts for YouTube videos into the second brain.

Usage:
  python pull_captions.py <videoId-or-URL> [more ids...]
  python pull_captions.py --playlist <playlist-URL-or-id>
  python pull_captions.py --all          # every video in youtube/playlists.json

Uses yt-dlp (already installed) to fetch manual subs, falling back to
auto-generated ones. Output: D:\\SecondBrain\\youtube\\captions\\<videoId>.md
Throttled ~3s between videos; skips videos already fetched or with no captions.
"""
import json, re, subprocess, sys, time
from pathlib import Path

OUT = Path(r"E:\SecondBrain\youtube\captions")
PLAYLISTS = Path(r"E:\SecondBrain\youtube\playlists.json")


def vid_of(s):
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", s)
    return m.group(1) if m else (s if re.fullmatch(r"[\w-]{11}", s) else None)


def vtt_to_text(vtt: str) -> str:
    lines, seen = [], set()
    for line in vtt.splitlines():
        line = line.strip()
        if (not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in line or line.isdigit()):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if line and line not in seen:
            seen.add(line)          # auto-subs repeat lines; dedupe consecutive
            lines.append(line)
    return "\n".join(lines)


def pull(video_id):
    out_md = OUT / f"{video_id}.md"
    if out_md.exists():
        return "cached"
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "_tmp"
    tmp.mkdir(exist_ok=True)
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = ["yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
           "--sub-langs", "en.*,hi.*", "--sub-format", "vtt", "--no-warnings",
           "--no-simulate", "--print", "%(title)s|%(channel)s",
           "-o", str(tmp / "%(id)s.%(ext)s"), url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return "timeout"
    meta = (r.stdout or "").strip().splitlines()
    title, channel = (meta[-1].split("|", 1) + [""])[:2] if meta else (video_id, "")
    vtts = sorted(tmp.glob(f"{video_id}*.vtt"))
    if not vtts:
        return "no-captions"
    manual = [v for v in vtts if ".en." in v.name or not re.search(r"\.[a-z]{2}(-[a-zA-Z]+)?\.vtt$", v.name)]
    src = manual[0] if manual else vtts[0]
    text = vtt_to_text(src.read_text(encoding="utf-8", errors="replace"))
    for v in vtts:
        v.unlink()
    if not text.strip():
        return "empty"
    out_md.write_text(
        f"# {title}\n\n_Channel: {channel} — https://youtu.be/{video_id}_\n\n{text}\n",
        encoding="utf-8")
    return "ok"


def all_ids_from_playlists():
    data = json.loads(PLAYLISTS.read_text(encoding="utf-8"))
    return [v["videoId"] for pl in data for v in pl["videos"]]


def playlist_ids(url):
    r = subprocess.run(["yt-dlp", "--flat-playlist", "--print", "%(id)s", url],
                       capture_output=True, text=True, timeout=300)
    return [l.strip() for l in r.stdout.splitlines() if re.fullmatch(r"[\w-]{11}", l.strip())]


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    if args[0] == "--all":
        ids = all_ids_from_playlists()
    elif args[0] == "--playlist":
        ids = playlist_ids(args[1])
    else:
        ids = [vid_of(a) for a in args]
    ids = [i for i in ids if i]
    print(f"{len(ids)} videos to fetch")
    for i, vid in enumerate(ids, 1):
        status = pull(vid)
        print(f"[{i}/{len(ids)}] {vid}: {status}")
        if status not in ("cached",):
            time.sleep(3)
