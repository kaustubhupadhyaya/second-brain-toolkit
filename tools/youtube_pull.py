"""Pull YouTube playlists into the second brain.

Usage:
  python youtube_pull.py auth        # one-time OAuth (opens browser)
  python youtube_pull.py playlists   # dump playlists.json + per-playlist .md
  python youtube_pull.py takeout <playlists-csv-dir>   # import Takeout playlist CSVs
                                                       # (covers SAVED playlists the API omits)

Credentials: reads GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from
C:\\Users\\Admin\\.secrets\\creds.env ; token cached at .secrets\\youtube_token.json
Requires: pip install google-api-python-client google-auth-oauthlib
"""
import csv, json, sys
from pathlib import Path

SECRETS = Path(r"C:\Users\Admin\.secrets")
OUT = Path(r"E:\SecondBrain\youtube")
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]


def env():
    d = {}
    for line in (SECRETS / "creds.env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def get_creds(interactive=False):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    token_file = SECRETS / "youtube_token.json"
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    if (not creds or not creds.valid) and interactive:
        from google_auth_oauthlib.flow import InstalledAppFlow
        e = env()
        cfg = {"installed": {
            "client_id": e["GOOGLE_CLIENT_ID"], "client_secret": e["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]}}
        flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
        creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json())
    if not creds or not creds.valid:
        sys.exit("No valid token. Run: python youtube_pull.py auth")
    return creds


def yt():
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=get_creds())


def pull_playlists():
    api = yt()
    playlists, page = [], None
    while True:
        r = api.playlists().list(part="snippet,contentDetails", mine=True,
                                 maxResults=50, pageToken=page).execute()
        playlists += r.get("items", [])
        page = r.get("nextPageToken")
        if not page:
            break
    OUT.mkdir(parents=True, exist_ok=True)
    dump = []
    for pl in playlists:
        title = pl["snippet"]["title"]
        items, page = [], None
        while True:
            r = api.playlistItems().list(part="snippet,contentDetails", playlistId=pl["id"],
                                         maxResults=50, pageToken=page).execute()
            items += r.get("items", [])
            page = r.get("nextPageToken")
            if not page:
                break
        vids = [{"videoId": i["contentDetails"]["videoId"],
                 "title": i["snippet"]["title"],
                 "channel": i["snippet"].get("videoOwnerChannelTitle", "")} for i in items]
        dump.append({"id": pl["id"], "title": title, "videos": vids})
        md = [f"# Playlist: {title}", ""]
        md += [f"- [{v['title']}](https://youtu.be/{v['videoId']}) — {v['channel']}" for v in vids]
        safe = "".join(c for c in title if c.isalnum() or c in " -_")[:60].strip() or pl["id"]
        (OUT / f"playlist_{safe}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    (OUT / "playlists.json").write_text(json.dumps(dump, indent=2), encoding="utf-8")
    print(f"youtube: {len(playlists)} playlists, {sum(len(p['videos']) for p in dump)} videos -> {OUT}")


def import_takeout(csv_dir):
    """Takeout > YouTube > playlists: one CSV per playlist, rows = video IDs."""
    OUT.mkdir(parents=True, exist_ok=True)
    for f in Path(csv_dir).glob("*.csv"):
        rows = list(csv.DictReader(f.open(encoding="utf-8-sig")))
        ids = [r.get("Video ID", "").strip() for r in rows if r.get("Video ID", "").strip()]
        if not ids:
            continue
        md = [f"# Playlist (Takeout): {f.stem}", ""]
        md += [f"- https://youtu.be/{vid}" for vid in ids]
        (OUT / f"playlist_takeout_{f.stem[:60]}.md").write_text("\n".join(md) + "\n", encoding="utf-8")
        print(f"takeout playlist {f.stem}: {len(ids)} videos")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auth":
        get_creds(interactive=True); print("OAuth token saved.")
    elif cmd == "playlists":
        pull_playlists()
    elif cmd == "takeout" and len(sys.argv) > 2:
        import_takeout(sys.argv[2])
    else:
        print(__doc__)
