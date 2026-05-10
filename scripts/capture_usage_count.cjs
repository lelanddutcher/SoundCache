#!/usr/bin/env node
const { chromium } = require('/opt/data/node_modules/playwright');

function parseUsage(text) {
  const match = String(text || '').match(/(\d+(?:\.\d+)?)\s*([KMB])?\s+videos?\b/i);
  if (!match) return null;
  const mult = { '': 1, K: 1000, M: 1000000, B: 1000000000 }[(match[2] || '').toUpperCase()];
  return { usage_count: Math.round(parseFloat(match[1]) * mult), usage_count_label: match[0].trim() };
}

async function main() {
  const [url, storageStatePath] = process.argv.slice(2);
  if (!url) {
    console.error('usage: capture_usage_count.cjs <music-url> [storage-state]');
    process.exit(2);
  }
  const browser = await chromium.launch({ headless: true });
  const contextOptions = {
    viewport: { width: 1365, height: 900 },
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
  };
  if (storageStatePath) contextOptions.storageState = storageStatePath;
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    await page.waitForTimeout(3500);
    const payload = await page.evaluate(() => {
      const texts = [];
      const push = (value) => { if (value && String(value).trim()) texts.push(String(value).trim()); };
      push(document.title);
      push(document.body ? document.body.innerText : '');
      document.querySelectorAll('meta[property],meta[name]').forEach((el) => push(el.getAttribute('content')));
      const scripts = Array.from(document.querySelectorAll('script'))
        .map((el) => el.textContent || '')
        .filter((text) => /videos?|playCount|videoCount|useCount|music/.test(text));
      push(scripts.join('\n').slice(0, 250000));
      return { final_url: location.href, title: document.title, texts };
    });
    let found = null;
    let source = '';
    for (let i = 0; i < payload.texts.length; i += 1) {
      found = parseUsage(payload.texts[i]);
      if (found) { source = `dom_text_${i}`; break; }
    }
    console.log(JSON.stringify({ ok: Boolean(found), ...payload, ...(found || {}), source }, null, 2));
  } catch (err) {
    console.log(JSON.stringify({ ok: false, error: String(err && err.message || err) }, null, 2));
    process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main();
