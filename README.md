# second-brain-toolkit

Companion to the plan at `C:\Users\Admin\.claude\plans\i-want-to-use-synthetic-shannon.md`
(Phase 2.4 converters + export automation). Lives under the GitHub root because
GitHub = the code bucket; Drive = text, D: = video/images.

## What is NOT in here (deliberate)

No `creds.env`, no passwords, no MFA relay. Request to store live
Gmail/Apple ID/Microsoft/ChatGPT/Claude passwords for autonomous Playwright
logins was declined 2026-09-06 and is declined again here:

- A human relaying a live MFA code to an automated login is the OTP-relay
  attack pattern, regardless of bot-detection.
- No capability gain: ChatGPT/Claude export is a button click that queues an
  emailed zip either way, automated or manual.

Use `export-clicker/` instead: you log in once manually, automation only
re-clicks export buttons inside your own session.

## Layout

- `tools/convert_codex.py` — `~/.codex/sessions/**/*.jsonl` → `chats/codex/`
- `tools/convert_copilot_chat.py` — Cursor + VS Code Copilot Chat
  `workspaceStorage/*/chatSessions/*.json` → `chats/cursor/`, `chats/vscode-copilot/`
  (same schema, one parser)
- `tools/convert_warp.py` — `warp.sqlite` (`ai_queries`+`ai_blocks`) → `chats/warp/`
  (user prompts are server-side only; converter keeps assistant text + tool summary)
- `export-clicker/login_once.py` → manual login into persistent `./profile/`
- `export-clicker/click_exports.py` → reuses profile, export clicks only
- `.env.example` — non-secret config template (paths/labels only)

## Run

```
pip install playwright && playwright install chromium
python export-clicker/login_once.py      # you log in manually, then close
python export-clicker/click_exports.py   # export clicks only, no passwords
python tools/convert_codex.py --out C:\SecondBrainStaging\SecondBrain\chats
python tools/convert_copilot_chat.py --out C:\SecondBrainStaging\SecondBrain\chats
python tools/convert_warp.py --out C:\SecondBrainStaging\SecondBrain\chats
```

Promote output into `G:\My Drive\SecondBrain\00_Inbox\` per its AGENTS.md
(frontmatter `title/tags/created/updated/summary`, quoted `summary`), then
update each folder's INDEX.md.
