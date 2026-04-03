/**
 * screenshot_dashboard_de.js
 * --------------------------
 * Takes a full-page screenshot of the German-localized dashboard.
 * Based on screenshot_dashboard.js but loads index.de.html via port 7824.
 *
 * Usage:
 *   node screenshot_dashboard_de.js --json dashboard/dashboard_data.json [--out reports/2026-04-03/de] [--url http://localhost:7824]
 *
 * Outputs:
 *   reports/YYYY-MM-DD/de/00_full_dashboard_de.png
 */

'use strict';

const puppeteer = require('puppeteer');
const path      = require('path');
const fs        = require('fs');

// ── CLI args ──────────────────────────────────────────────────
const args = process.argv.slice(2);
function getArg(flag, def = null) {
  const i = args.indexOf(flag);
  return i >= 0 && args[i+1] ? args[i+1] : def;
}
const jsonPath  = getArg('--json',  'dashboard/dashboard_data.json');
const outDir    = getArg('--out',   `reports/${new Date().toISOString().slice(0,10)}/de`);
const serverUrl = getArg('--url',   'http://localhost:7824');
const width     = parseInt(getArg('--width', '1440'), 10);

// ── Helpers ───────────────────────────────────────────────────
function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function waitForCharts(page) {
  await page.waitForFunction(() => {
    const canvases = document.querySelectorAll('canvas');
    if (canvases.length === 0) return false;
    return [...canvases].every(c => c.width > 0 && c.height > 0);
  }, { timeout: 15000 });
  // Extra settle time for animations
  await new Promise(r => setTimeout(r, 1200));
}

async function injectData(page, jsonFilePath) {
  const data = JSON.parse(fs.readFileSync(jsonFilePath, 'utf8'));
  await page.evaluate((d) => {
    if (typeof loadData === 'function') {
      loadData(d);
    }
  }, data);
}

// ── Main ──────────────────────────────────────────────────────
(async () => {
  console.log(`\n📸 Roboadvisor German Dashboard Screenshot`);
  console.log(`   JSON:   ${jsonPath}`);
  console.log(`   Output: ${outDir}`);
  console.log(`   URL:    ${serverUrl}`);
  console.log(`   Width:  ${width}px\n`);

  if (!fs.existsSync(jsonPath)) {
    console.error(`ERROR: JSON file not found: ${jsonPath}`);
    process.exit(1);
  }

  ensureDir(outDir);

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: 'new',
      args: ['--no-sandbox', '--disable-setuid-sandbox', `--window-size=${width},900`],
    });

    const page = await browser.newPage();
    await page.setViewport({ width, height: 900, deviceScaleFactor: 2 });

    // Load the German dashboard (index.de.html served at root)
    const dashboardUrl = `${serverUrl}/index.de.html`;
    console.log(`Loading ${dashboardUrl} ...`);
    await page.goto(dashboardUrl, { waitUntil: 'networkidle0', timeout: 30000 });

    // Inject the JSON data
    console.log('Injecting dashboard data...');
    await injectData(page, jsonPath);

    // Wait for charts to finish rendering
    console.log('Waiting for charts to render...');
    await waitForCharts(page);

    // Capture full dashboard screenshot
    const fullPath = path.join(outDir, '00_full_dashboard_de.png');
    await page.evaluate(() => window.scrollTo(0, 0));
    await new Promise(r => setTimeout(r, 300));
    await page.screenshot({ path: fullPath, fullPage: true, type: 'png' });
    console.log(`  ✅ 00_full_dashboard_de.png (full page)`);

    console.log(`\n✅ Done. Screenshot saved to: ${outDir}\n`);
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
})();
