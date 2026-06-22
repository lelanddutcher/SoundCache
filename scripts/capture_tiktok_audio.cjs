#!/usr/bin/env node
/**
 * Capture a TikTok original-sound's audio (and metadata) via an authenticated
 * Playwright session. yt-dlp's TikTok "sound" extractor is broken, so this drives
 * a real browser (using a logged-in storageState) to intercept the playable media,
 * then ffmpeg extracts a clean m4a. It also scrapes title/creator/cover/usage from
 * the same page load and writes a <music_id>_meta.json sidecar.
 *
 * Usage: node capture_tiktok_audio.cjs <url> <out_folder> <music_id> <storage_state> [mode]
 *   mode = "meta-only" → scrape metadata + cover ONLY (no audio download). Used by
 *   the in-app "re-enrich incomplete" worker to refresh metadata cheaply.
 * Writes <out_folder>/<music_id>_raw.m4a (unless meta-only) + <music_id>_meta.json.
 * Run from a directory where `require('playwright')` resolves (this repo's root).
 */
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const { chromium } = require("playwright");

// Scrape sound metadata + cover from the loaded music page; write the sidecar.
// Best-effort: never throws out — returns true/false.
async function scrapeAndWriteMeta(page, outFolder, musicId, url) {
  try {
    const meta = await page.evaluate(() => {
      const txt = (sel) => { const el = document.querySelector(sel); return el ? (el.textContent || "").trim() : ""; };
      const attr = (sel, a) => { const el = document.querySelector(sel); return el ? (el.getAttribute(a) || "") : ""; };
      const og = (p) => attr(`meta[property="${p}"]`, "content") || attr(`meta[name="${p}"]`, "content");
      let title = txt('h1[data-e2e="music-title"]') || txt("h1");
      let author = txt('h2[data-e2e="music-creator"]') || txt('[data-e2e="music-creator"] a');
      const ogTitle = og("og:title");
      if ((!title || !author) && ogTitle) {
        const cleaned = ogTitle.replace(/\s*\|\s*TikTok.*$/i, "").trim();
        const parts = cleaned.split(" - ");
        if (!title) title = (parts[0] || cleaned).trim();
        if (!author && parts.length > 1) author = parts.slice(1).join(" - ").trim();
      }
      const coverUrl = attr('[data-e2e="music-cover"] img', "src") || attr('img[class*="ImgCover"]', "src") || og("og:image");
      let usage = "";
      for (const el of Array.from(document.querySelectorAll('[data-e2e="music-video-count"], strong, h2'))) {
        const t = (el.textContent || "").trim();
        if (/^[\d.,]+\s*[kKmMbB]?\s*(videos?|posts?)$/i.test(t)) { usage = t; break; }
      }
      // The creator h2 / og:title sometimes carries the usage count (e.g.
      // "Toni_SP11 ∙ 8335 videos") — strip a trailing "· N videos/posts" so the
      // author/title don't get polluted (and the on-disk folder name stays clean).
      const stripUsage = (s) =>
        (s || "").replace(/\s*[·∙•|/–-]+\s*[\d.,]+\s*[kKmMbB]?\s*(?:videos?|posts?|clips?)\b.*$/i, "").trim();
      title = stripUsage(title);
      author = stripUsage(author);
      return { title, author, coverUrl, usage, pageUrl: location.href };
    });
    const parseCount = (s) => {
      if (!s) return null;
      const m = String(s).match(/([\d.,]+)\s*([kKmMbB]?)/);
      if (!m) return null;
      let n = parseFloat(m[1].replace(/,/g, ""));
      const suf = (m[2] || "").toLowerCase();
      if (suf === "k") n *= 1e3; else if (suf === "m") n *= 1e6; else if (suf === "b") n *= 1e9;
      return Math.round(n);
    };
    let coverBase = "";
    if (meta.coverUrl) {
      try {
        const cr = await page.context().request.fetch(meta.coverUrl);
        const cbuf = await cr.body();
        const ext = ((meta.coverUrl.split("?")[0].match(/\.(jpe?g|png|webp)$/i) || [null, "jpg"])[1]).toLowerCase();
        coverBase = `${musicId}_cover.${ext}`;
        fs.writeFileSync(path.join(outFolder, coverBase), cbuf);
      } catch (e) { coverBase = ""; }
    }
    const metaOut = {
      title: meta.title || "",
      author: meta.author || "",
      coverUrl: meta.coverUrl || "",
      coverPath: coverBase,
      usageCount: parseCount(meta.usage),
      pageUrl: meta.pageUrl || url,
    };
    fs.writeFileSync(path.join(outFolder, `${musicId}_meta.json`), JSON.stringify(metaOut, null, 2));
    return true;
  } catch (e) {
    console.error("meta scrape skipped:", e && e.message ? e.message : String(e));
    return false;
  }
}

(async () => {
  const [url, outFolder, musicId, storageState, mode] = process.argv.slice(2);
  if (!url || !outFolder || !musicId) {
    console.error("usage: capture_tiktok_audio.cjs <url> <out_folder> <music_id> <storage_state> [mode]");
    process.exit(2);
  }
  const metaOnly = mode === "meta-only";

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

    if (metaOnly) {
      await scrapeAndWriteMeta(page, outFolder, musicId, url);
      console.log(path.join(outFolder, `${musicId}_meta.json`));
      await browser.close();
      process.exit(0);
    }

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
      await scrapeAndWriteMeta(page, outFolder, musicId, url);
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
