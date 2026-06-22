#!/usr/bin/env node
/**
 * Interactive TikTok login. Opens a real TikTok login window so the user signs in
 * normally, then saves the browser session (Playwright storageState) locally so
 * Sound Cache can capture sound audio — which TikTok only serves to a logged-in
 * session. The password is typed into TikTok's own page; it never passes through
 * this script. Only the resulting session cookies are written to <out_state_path>.
 *
 * Usage: node tiktok_login.cjs <out_state_path>
 * Exit:  0 = saved a logged-in session
 *        2 = bad usage     5 = unexpected error
 *        6 = window closed before login completed
 *        7 = timed out waiting for login
 * Run from a dir where require('playwright') resolves (this repo root).
 */
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const OUT = process.argv[2];
const LOGIN_URL = "https://www.tiktok.com/login";
const TIMEOUT_MS = 5 * 60 * 1000; // give the user 5 minutes to sign in
const POLL_MS = 1500;

const hasSession = (cookies) =>
  cookies.some(
    (c) => (c.name === "sessionid" || c.name === "sessionid_ss") && c.value && c.value.length > 6
  );

(async () => {
  if (!OUT) {
    console.error("usage: tiktok_login.cjs <out_state_path>");
    process.exit(2);
  }
  fs.mkdirSync(path.dirname(OUT), { recursive: true });

  const browser = await chromium.launch({
    headless: false,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const context = await browser.newContext({
    viewport: { width: 1180, height: 820 },
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  });

  let saved = false;
  const save = async (reason) => {
    if (saved) return;
    try {
      const cookies = await context.cookies();
      if (!hasSession(cookies)) return;
      await context.storageState({ path: OUT });
      saved = true;
      console.error("saved session (" + reason + ")");
    } catch (e) {
      /* context may be tearing down */
    }
  };

  // If the user just closes the window, exit with the right code.
  browser.on("disconnected", () => process.exit(saved ? 0 : 6));

  const page = await context.newPage();
  try {
    await page.goto(LOGIN_URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  } catch (e) {
    /* slow network — keep polling for the session anyway */
  }
  console.error("login window open");

  const start = Date.now();
  while (Date.now() - start < TIMEOUT_MS) {
    await new Promise((r) => setTimeout(r, POLL_MS));
    let cookies = [];
    try {
      cookies = await context.cookies();
    } catch (e) {
      break; // browser gone — disconnected handler will exit
    }
    if (hasSession(cookies)) {
      await new Promise((r) => setTimeout(r, 1500)); // let all auth cookies land
      await save("detected login");
      break;
    }
  }

  try {
    await browser.close();
  } catch (e) {
    /* ignore */
  }
  process.exit(saved ? 0 : 7);
})().catch((e) => {
  console.error("login error:", e && e.message ? e.message : String(e));
  process.exit(5);
});
