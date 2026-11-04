#!/usr/bin/env node
// Check the Chromium we bundle actually works the way the capture scripts use it.
//
//   PLAYWRIGHT_BROWSERS_PATH=<app>/Contents/Resources/ms-playwright \
//     node scripts/verify_bundled_browser.cjs <expected-version>
//
// Launches through launch_chromium.cjs (sandbox on, as in the app), renders a page,
// prints the browser version, and on macOS fails if any renderer runs --no-sandbox.
"use strict";
const { execFileSync } = require("child_process");
const { chromium } = require("playwright");
const { launchChromium } = require("./launch_chromium.cjs");

(async () => {
  const expected = process.argv[2];
  const browser = await launchChromium(chromium, { headless: true, channel: "chromium" });
  try {
    const version = browser.version();
    console.log(`browser version: ${version}`);
    if (expected && version !== expected) {
      throw new Error(`expected bundled Chrome ${expected}, got ${version}`);
    }
    const page = await (await browser.newContext()).newPage();
    await page.setContent("<h1 id=t>sound cache</h1>");
    const text = await page.textContent("#t");
    if (text !== "sound cache") throw new Error(`page did not render: ${text}`);
    if (process.platform === "darwin") {
      const ps = execFileSync("ps", ["-axww", "-o", "command="], { encoding: "utf8" });
      const renderers = ps.split("\n").filter((l) => l.includes("--type=renderer") && l.includes("Chrome for Testing"));
      if (renderers.length === 0) throw new Error("no renderer process found");
      const unsandboxed = renderers.filter((l) => l.includes("--no-sandbox"));
      if (unsandboxed.length) throw new Error(`${unsandboxed.length} renderer(s) running --no-sandbox`);
      console.log(`sandboxed renderers: ${renderers.length}`);
    }
    console.log("ok");
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error(String(err && err.stack || err));
  process.exit(1);
});
