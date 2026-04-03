/**
 * screenshot_dashboard_de.js
 * --------------------------
 * Takes a full-page screenshot of the German-localized dashboard.
 * Uses viewport-chunk strategy to work around Puppeteer's fullPage timeout
 * on Canvas-heavy pages. Stitches chunks with sharp.
 *
 * Usage:
 *   node screenshot_dashboard_de.js --json dashboard/dashboard_data.json [--out reports/2026-04-03/de] [--url http://localhost:7824]
 *
 * Outputs:
 *   reports/YYYY-MM-DD/de/00_full_dashboard_de.jpg
 */

'use strict';

const puppeteer = require('puppeteer');
const sharp     = require('sharp');
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
const chunkH    = parseInt(getArg('--chunk-height', '600'), 10);

// ── Helpers ───────────────────────────────────────────────────
function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function waitForDashboard(page) {
  try {
    await page.waitForFunction(() => window.__chartsReady === true, { timeout: 25000 });
    console.log('  ✅ Charts ready signal received');
  } catch (e) {
    console.warn('  ⚠️  __chartsReady timeout — proceeding anyway...');
  }
  await new Promise(r => setTimeout(r, 1000));
}

async function captureChunks(page, pageHeight, width, chunkH, tmpDir) {
  const chunks = [];
  let y = 0;
  let idx = 0;

  while (y < pageHeight) {
    const h = Math.min(chunkH, pageHeight - y);
    await page.evaluate((scrollY) => window.scrollTo(0, scrollY), y);
    await new Promise(r => setTimeout(r, 200));

    const chunkPath = path.join(tmpDir, `chunk_${idx}.jpg`);
    await page.screenshot({
      path: chunkPath,
      type: 'jpeg',
      quality: 88,
      clip: { x: 0, y: 0, width, height: h },
    });
    chunks.push({ path: chunkPath, y, height: h });
    console.log(`  📷 Chunk ${idx}: y=${y}, h=${h}`);
    y += h;
    idx++;
  }
  return chunks;
}

async function stitchChunks(chunks, width, pageHeight, outPath) {
  const composites = await Promise.all(chunks.map(async (c) => ({
    input: c.path,
    top: c.y,
    left: 0,
  })));

  await sharp({
    create: {
      width,
      height: pageHeight,
      channels: 3,
      background: { r: 15, g: 15, b: 15 },
    }
  })
  .composite(composites)
  .jpeg({ quality: 88 })
  .toFile(outPath);

  // Cleanup tmp chunks
  chunks.forEach(c => fs.unlinkSync(c.path));
}

// ── Main ──────────────────────────────────────────────────────
(async () => {
  console.log(`\n📸 Roboadvisor German Dashboard Screenshot`);
  console.log(`   JSON:   ${jsonPath}`);
  console.log(`   Output: ${outDir}`);
  console.log(`   URL:    ${serverUrl}`);
  console.log(`   Width:  ${width}px | Chunk: ${chunkH}px\n`);

  if (!fs.existsSync(jsonPath)) {
    console.error(`ERROR: JSON file not found: ${jsonPath}`);
    process.exit(1);
  }

  ensureDir(outDir);
  const tmpDir = path.join(outDir, '_tmp');
  ensureDir(tmpDir);

  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,           // legacy headless (more stable for canvas/screenshots)
      protocolTimeout: 300000,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-web-security',
        '--disable-gpu',        // force software rendering
        '--use-gl=swiftshader', // software WebGL
        '--disable-software-rasterizer',
        '--disable-dev-shm-usage',
        `--window-size=${width},${chunkH}`,
      ],
    });

    const page = await browser.newPage();
    await page.setViewport({ width, height: chunkH, deviceScaleFactor: 1 });

    const dashboardUrl = `${serverUrl}/index.de.html`;
    console.log(`Loading ${dashboardUrl} ...`);
    await page.goto(dashboardUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

    console.log('Waiting for dashboard to render...');
    await waitForDashboard(page);

    // Get real page height
    const pageHeight = await page.evaluate(() => document.body.scrollHeight);
    console.log(`  📏 Page height: ${pageHeight}px`);

    // Capture in chunks (avoids fullPage timeout)
    console.log(`  Capturing ${Math.ceil(pageHeight / chunkH)} chunk(s)...`);
    const chunks = await captureChunks(page, pageHeight, width, chunkH, tmpDir);

    // Stitch together
    const outPath = path.join(outDir, '00_full_dashboard_de.jpg');
    console.log(`  Stitching chunks...`);
    await stitchChunks(chunks, width, pageHeight, outPath);

    console.log(`\n✅ Done: ${outPath}\n`);
    // Print path for callers
    process.stdout.write(outPath + '\n');

  } finally {
    if (browser) await browser.close().catch(() => {});
    // Clean up tmp dir if empty
    try { fs.rmdirSync(path.join(outDir, '_tmp')); } catch(e) {}
  }
})();
