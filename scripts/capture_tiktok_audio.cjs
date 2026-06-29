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
      // TikTok shows an "Add to Spotify"/"Listen on Spotify" link for published
      // tracks — capture the canonical open.spotify.com track/album/artist URL so the
      // app can offer an "Open in Spotify" button. Prefer a track link; strip query.
      let spotifyUrl = "";
      const spotifyAnchors = Array.from(document.querySelectorAll('a[href*="spotify.com"]'));
      for (const a of spotifyAnchors) {
        const href = (a.getAttribute("href") || "").trim();
        if (/open\.spotify\.com\/(track|album|artist)\//i.test(href)) { spotifyUrl = href.split("?")[0]; break; }
      }
      if (!spotifyUrl && spotifyAnchors.length) {
        spotifyUrl = (spotifyAnchors[0].getAttribute("href") || "").trim().split("?")[0];
      }
      return { title, author, coverUrl, usage, pageUrl: location.href, spotifyUrl };
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
    // Also read the structured `music` object from the page rehydration JSON.
    // Video/photo pages SSR it (gives the authoritative sound id + original flag +
    // full duration); /music/ pages don't, so this is simply absent there. Additive
    // + fully guarded — never breaks the DOM scrape above.
    let musicJson = {};
    try {
      musicJson = await page.evaluate(() => {
        try {
          const node = document.getElementById("__UNIVERSAL_DATA_FOR_REHYDRATION__");
          if (!node) return {};
          const raw = node.textContent || "{}";
          const data = JSON.parse(raw);
          const scope = data.__DEFAULT_SCOPE__ || {};
          const item = ((scope["webapp.video-detail"] || {}).itemInfo || {}).itemStruct || {};
          const m = item.music || {};
          // Fallback: pull a Spotify track/album link straight out of the SSR JSON
          // text (TikTok's DSP-link payload) when no DOM anchor was rendered.
          let spotifyUrl = "";
          const sm = raw.match(/https:\\?\/\\?\/open\.spotify\.com\\?\/(?:track|album|artist)\\?\/[A-Za-z0-9]+/);
          if (sm) spotifyUrl = sm[0].replace(/\\\//g, "/");
          if (!m.id) return { spotifyUrl };
          return {
            musicId: String(m.id),
            original: typeof m.original === "boolean" ? m.original : null,
            soundDuration: typeof m.duration === "number" ? m.duration : null,
            album: m.album || "",
            mTitle: m.title || "",
            mAuthor: m.authorName || "",
            playUrl: typeof m.playUrl === "string" ? m.playUrl : "",
            spotifyUrl,
          };
        } catch (e) { return {}; }
      });
    } catch (e) { musicJson = {}; }

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
      title: meta.title || musicJson.mTitle || "",
      author: meta.author || musicJson.mAuthor || "",
      coverUrl: meta.coverUrl || "",
      coverPath: coverBase,
      usageCount: parseCount(meta.usage),
      pageUrl: meta.pageUrl || url,
      // Structured sound facts from the rehydration JSON (when on a video/photo page).
      structuredMusicId: musicJson.musicId || "",
      original: typeof musicJson.original === "boolean" ? musicJson.original : null,
      soundDuration: musicJson.soundDuration != null ? musicJson.soundDuration : null,
      album: musicJson.album || "",
      spotifyUrl: meta.spotifyUrl || musicJson.spotifyUrl || "",
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

    // Pull the catalog playUrl from the page rehydration JSON (video pages SSR it);
    // for a commercial track this is the full ~60s preview the clip-only path misses.
    let jsonPlayUrl = "";
    try {
      jsonPlayUrl = await page.evaluate(() => {
        try {
          const node = document.getElementById("__UNIVERSAL_DATA_FOR_REHYDRATION__");
          const data = JSON.parse((node && node.textContent) || "{}");
          const item = (((data.__DEFAULT_SCOPE__ || {})["webapp.video-detail"] || {}).itemInfo || {}).itemStruct || {};
          return ((item.music || {}).playUrl) || "";
        } catch (e) { return ""; }
      });
    } catch (e) { jsonPlayUrl = ""; }

    const ranked = Array.from(new Set([jsonPlayUrl, ...domSources, ...mediaUrls].filter(Boolean)));
    // Probe audio-ish / catalog URLs first, but ultimately keep whichever yields the
    // LONGEST audio (a full /music/ sound beats a trimmed clip).
    const ordered = [
      ...ranked.filter((u) => /mime_type=audio_mpeg|mime_type=audio|\.m4a|\.mp3|\.aac|\/obj\//i.test(u)),
      ...ranked.filter((u) => /tiktokcdn|tiktokv/i.test(u) && !/playback\d*\.mp4|webapp|static/i.test(u)),
      ...ranked,
    ];
    const candidates = Array.from(new Set(ordered)).slice(0, 5); // cap fetch/transcode cost

    if (!candidates.length) {
      console.error("no media captured (page may require fresh auth)");
      await browser.close();
      process.exit(3);
    }

    const probeDuration = (p) => {
      try {
        const r = spawnSync("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", p]);
        const d = parseFloat(String((r.stdout || "")).trim());
        return isFinite(d) ? d : 0; // ffprobe missing/parse-fail → 0, so first success wins
      } catch (e) { return 0; }
    };

    const audioPath = path.join(outFolder, `${musicId}_raw.m4a`);
    let best = { path: null, dur: -1 };
    for (let i = 0; i < candidates.length; i++) {
      try {
        const resp = await page.context().request.fetch(candidates[i]);
        if (!resp.ok()) continue;
        const buf = await resp.body();
        if (!buf || buf.length < 1000) continue;
        const rawPath = path.join(outFolder, `${musicId}_raw_${i}`);
        fs.writeFileSync(rawPath, buf);
        const candPath = path.join(outFolder, `${musicId}_cand_${i}.m4a`);
        spawnSync("ffmpeg", ["-y", "-hide_banner", "-loglevel", "error", "-i", rawPath, "-vn", "-c:a", "aac", "-b:a", "192k", candPath]);
        try { fs.unlinkSync(rawPath); } catch (e) { /* ignore */ }
        if (fs.existsSync(candPath) && fs.statSync(candPath).size > 1000) {
          const dur = probeDuration(candPath);
          if (dur > best.dur) {
            if (best.path) { try { fs.unlinkSync(best.path); } catch (e) { /* ignore */ } }
            best = { path: candPath, dur };
          } else {
            try { fs.unlinkSync(candPath); } catch (e) { /* ignore */ }
          }
        }
      } catch (e) { /* try the next candidate */ }
    }

    if (best.path) {
      fs.renameSync(best.path, audioPath);
      await scrapeAndWriteMeta(page, outFolder, musicId, url);
      console.error(`captured audio (${best.dur > 0 ? best.dur.toFixed(1) + "s" : "len?"}) from ${candidates.length} candidate(s)`);
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
