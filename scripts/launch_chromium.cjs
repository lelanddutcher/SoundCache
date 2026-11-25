// Shared Chromium launch for the capture scripts.
//
// Playwright starts Chromium with its sandbox OFF by default (it passes --no-sandbox).
// These scripts load pages from the open web (TikTok and the links people share), so a
// renderer exploit there should stay inside Chromium's sandbox. macOS supports the
// sandbox with no setup, so we ask for it there. If this machine can't start a
// sandboxed browser, we say so on stderr and fall back rather than break capture.
"use strict";

async function launchChromium(chromium, options) {
  if (process.platform !== "darwin" || process.env.SOUND_VAULT_CHROMIUM_NO_SANDBOX === "1") {
    return chromium.launch(options);
  }
  try {
    return await chromium.launch({ ...options, chromiumSandbox: true });
  } catch (err) {
    console.error(`[launch] sandboxed Chromium failed to start, retrying without the sandbox: ${err && err.message}`);
    return chromium.launch(options);
  }
}

module.exports = { launchChromium };
