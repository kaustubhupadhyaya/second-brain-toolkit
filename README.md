# second-brain-toolkit

Code for the second-brain vault (`G:\My Drive\SecondBrain`). The plan of record is in the vault:
`10_Projects\second-brain-consolidation\plan-v3.md`, with status in `goal-tracker.md`. Lives under the GitHub root
because GitHub = the code bucket; Drive = text, D: = video/images.

Since 2026-10-09 this repo is the canonical home of every vault tool (copies on `E:\SecondBrain\_tools` and
`C:\SecondBrainStaging\SecondBrain\_tools` are superseded). Note-filing, moving, indexing and the convention check
live in the `second-brain` skill (`agent-config/skills/second-brain/scripts/brain.py`), which these tools call.

## Scheduled tasks

| Task | Runs | Does |
|---|---|---|
| `SecondBrain-Gemini-HourlySync` | hourly | `tools/sync_live_gemini.py` (facecard venv): live Gemini chats to `E:\SecondBrain\chats\gemini`, then mirrored into the vault `30_Resources/ai-chats`; exit 1 on failure, alert note in `00_Inbox` after 3 failed hours |
| `SecondBrain-Harvester` | 23:00 | `tools/second_brain_harvester.mjs`: local agent session logs to `20_Areas/daily-logs`, health and career rollups, review items in `00_Inbox/entities` |
| `SecondBrain-PlansBackup` | 20:45 | `brain.py backup-plans`: `~/.claude/plans` into the vault `10_Projects/agent-plans` |

The Gemini sync stages on `E:\SecondBrain` when E: is attached and on `C:\SecondBrainStaging\SecondBrain` otherwise; its
log and failure counter are always `C:\SecondBrainStaging\_logs` (an unplugged E: once made it crash silently, 2026-10-21).
The alert note needs the vault (G:) mounted; Google Drive for Desktop must be running for that.

All three run through `C:\Users\Admin\bin\runhidden\runhidden.exe` (no console window; exit codes reach Task Scheduler).
Personal data never goes in this repo: logs, harvester state (`.harvest_*`) and vault content are outside it or ignored.

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
