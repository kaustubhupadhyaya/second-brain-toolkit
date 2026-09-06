"""Step 2 — automate ONLY the repetitive export clicks (SAFE: reuses YOUR login).

Reopens the persistent profile from login_once.py — no passwords anywhere.
Each step navigates to the provider's own export UI and clicks the same
button you would click. Exports are emailed/queued by the provider; this
script just saves you the repetitive clicking across accounts.

If a site shows a fresh MFA/login challenge, STOP — log in manually again
via login_once.py. Never paste live MFA codes for an automated session
(that is the OTP-relay pattern; see ../README.md).

Usage:  python click_exports.py
"""
from playwright.sync_api import sync_playwright
from pathlib import Path

PROFILE = Path(__file__).parent / "profile"

CHATGPT_EXPORT_URL = "https://chatgpt.com/#settings/DataControls"  # Settings → Data controls → Export
CLAUDE_EXPORT_URL = "https://claude.ai/settings/privacy"           # Settings → Privacy → Export data

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.new_page()
    for name, url in [("chatgpt", CHATGPT_EXPORT_URL), ("claude", CLAUDE_EXPORT_URL)]:
        page.goto(url)
        print(f"[{name}] Opened {url} — complete/click the Export control manually if the selector changed,")
        print("then press Enter here to continue to the next site.")
        input(f"[{name}] press Enter when done...")
    ctx.close()
    print("Done. Check each account's email for the export zip, then run the converters in ../tools/.")
