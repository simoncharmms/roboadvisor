/**
 * screenshot_playwright.js
 * --------------------------
 * Takes a full-page screenshot of the German dashboard using Playwright.
 * Playwright has a more reliable screenshot implementation than Puppeteer
 * for Canvas-heavy pages on macOS ARM.
 *
 * Usage:
 *   node screenshot_playwright.js --out reports/2026-04-03/de [--url http://localhost:7824]
 *
 * Outputs:
 *   reports/YYYY-MM-DD/de/00_full_dashboard_de.jpg
 */

'use strict';

const { chromium } = require('playwright');
const path = require('path');
const fs   = require('fs');

// ── CLI args ──────────────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(flag, def = null) {
  const i = args.indexOf(flag);
  return i >= 0 && args[i+1] ? args[i+1] : def;
}
const outDir    = getArg('--out',   `reports/${new Date().toISOString().slice(0,10)}/de`);
const serverUrl = getArg('--url',   'http://127.0.0.1:7824');
const width     = parseInt(getArg('--width', '1440'), 10);

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

(async () => {
  console.log(`\n📸 Roboadvisor Dashboard Screenshot (Playwright)`);
  console.log(`   Output: ${outDir}`);
  console.log(`   URL:    ${serverUrl}`);
  console.log(`   Width:  ${width}px\n`);

  ensureDir(outDir);

  // Use full Chromium (not headless shell) for localhost access
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH ||
    `${process.env.HOME}/Library/Caches/ms-playwright/chromium-1217/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing`;

  const browser = await chromium.launch({
    executablePath,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-web-security',
      '--allow-insecure-localhost',
    ],
  });

  try {
    const page = await browser.newPage();
    await page.setViewportSize({ width, height: 900 });

    const dashboardUrl = `${serverUrl}/index.de.html`;
    console.log(`Loading ${dashboardUrl} ...`);
    await page.goto(dashboardUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

    console.log('Waiting for dashboard to render...');
    try {
      await page.waitForFunction(() => window.__chartsReady === true, { timeout: 25000 });
      console.log('  ✅ Charts ready');
    } catch (e) {
      console.warn('  ⚠️  __chartsReady timeout — proceeding anyway...');
    }
    await page.waitForTimeout(1500);

    const outPath = path.join(outDir, '00_full_dashboard_de.jpg');
    await page.screenshot({
      path: outPath,
      fullPage: true,
      type: 'jpeg',
      quality: 88,
    });

    console.log(`\n✅ Done: ${outPath}\n`);
    process.stdout.write(outPath + '\n');

  } finally {
    await browser.close();
  }
})();
