"""Step 1 — one-time manual login (SAFE: you type passwords yourself).

Opens a persistent Chromium profile at ./profile/. Log into ChatGPT /
Claude / Gmail in the opened window, check 'stay signed in', then close
the browser. Playwright NEVER sees your passwords — it just reuses the
cookies/session your own manual login created.

Usage:  python login_once.py
"""
from playwright.sync_api import sync_playwright
from pathlib import Path

PROFILE = Path(__file__).parent / "profile"

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE),
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto("https://chatgpt.com")
    print("Log in manually to each site you want exports from (ChatGPT, claude.ai, Gmail).")
    print("When done, close the browser window. Profile saved at:", PROFILE)
    page.wait_for_timeout(1000 * 60 * 30)  # 30 min window, then exits
    ctx.close()
