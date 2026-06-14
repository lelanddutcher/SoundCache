#!/usr/bin/env node
/**
 * Capture a TikTok original-sound's audio via an authenticated Playwright session.
 * yt-dlp's TikTok "sound" extractor is broken, so this drives a real browser
 * (using a logged-in storageState) to intercept the playable media, then ffmpeg
 * extracts a clean m4a.
 *
 * Usage: node capture_tiktok_audio.cjs <url> <out_folder> <music_id> <storage_state>
 * Writes <out_folder>/<music_id>_raw.m4a on success.
 * Run from a directory where `require('playwright')` resolves (this repo's root).
 */
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { chromium } = require("playwright");

(async () => {
  const [url, outFolder, musicId, storageState] = process.argv.slice(2);
  if (!url || !outFolder || !musicId) {
    console.error("usage: capture_tiktok_audio.cjs <url> <out_folder> <music_id> <storage_state>");
    process.exit(2);
  }

  const contextOptions = {
    viewport: { width: 1280, height: 900 },
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
  };
  if (storageState && fs.existsSync(storageState)) {
    contextOptions.storageState = storageState;
  }

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();

  const mediaUrls = [];
  page.on("response", (resp) => {
    const u = resp.url();
    const ct = resp.headers()["content-type"] || "";
    if (
      ct.includes("audio") || ct.includes("video") ||
      /\.m4a|\.mp3|\.aac|\.mp4|mime_type=audio/i.test(u)
    ) {
      if (!mediaUrls.includes(u)) mediaUrls.push(u);
    }
  });

  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForTimeout(7000);
    await page.evaluate(async () => {
      for (const el of Array.from(document.querySelectorAll("video,audio"))) {
        try { el.muted = false; await el.play(); } catch (e) { /* ignore */ }
      }
    });
    await page.waitForTimeout(3000);
    const domSources = await page.evaluate(() =>
      Array.from(document.querySelectorAll("video,audio"))
        .map((el) => el.currentSrc || el.src || "")
        .filter((s) => s && !s.startsWith("blob:"))
    );

    const ranked = Array.from(new Set([...domSources, ...mediaUrls]));
    const preferred =
      ranked.find((u) => /mime_type=audio_mpeg|mime_type=audio|\.m4a|\.mp3|\.aac/i.test(u)) ||
      ranked.find((u) => /tiktokcdn|tiktokv/i.test(u) && !/playback\d*\.mp4|webapp|static/i.test(u)) ||
      ranked[0];

    if (!preferred) {
      console.error("no media captured (page may require fresh auth)");
      await browser.close();
      process.exit(3);
    }
    console.error("captured media url");

    const mediaResp = await page.context().request.fetch(preferred);
    const buffer = await mediaResp.body();
    const rawPath = path.join(outFolder, `${musicId}_raw_download`);
    fs.writeFileSync(rawPath, buffer);

    const audioPath = path.join(outFolder, `${musicId}_raw.m4a`);
    spawnSync("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-i", rawPath, "-vn", "-c:a", "aac", "-b:a", "192k", audioPath]);
    if (fs.existsSync(audioPath) && fs.statSync(audioPath).size > 1000) {
      try { fs.unlinkSync(rawPath); } catch (e) { /* ignore */ }
      console.log(audioPath);
    } else {
      console.error("ffmpeg produced no audio stream");
      await browser.close();
      process.exit(4);
    }
  } catch (err) {
    console.error("capture error:", err && err.message ? err.message : String(err));
    await browser.close();
    process.exit(5);
  }
  await browser.close();
})();
