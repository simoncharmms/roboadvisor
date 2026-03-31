/**
 * screenshot_dashboard.js
 * -----------------------
 * Takes full-fidelity screenshots of every dashboard section and
 * optionally compiles them into a single PDF using PDFKit.
 *
 * Usage:
 *   node screenshot_dashboard.js --json dashboard/dashboard_data.json [--out reports/2026-03-31] [--pdf]
 *
 * Outputs:
 *   reports/YYYY-MM-DD/01_kpis.png
 *   reports/YYYY-MM-DD/02_performance.png
 *   reports/YYYY-MM-DD/03_allocation_signals.png
 *   reports/YYYY-MM-DD/04_backtest.png
 *   reports/YYYY-MM-DD/05_positions.png
 *   reports/YYYY-MM-DD/06_trades.png         (only if trades present)
 *   reports/YYYY-MM-DD/07_suggestions.png    (only if suggestions present)
 *   reports/YYYY-MM-DD/report.pdf            (if --pdf flag set)
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
const outDir    = getArg('--out',   `reports/${new Date().toISOString().slice(0,10)}`);
const makePdf   = args.includes('--pdf');
const serverUrl = getArg('--url',   'http://localhost:7823');
const width     = parseInt(getArg('--width', '1440'), 10);

// ── Sections to capture: [id_of_element_or_selector, output_name] ──
const SECTIONS = [
  { sel: '.kpi-strip',              name: '01_kpis'              },
  { sel: '#chart-performance',      name: '02_performance_chart' },
  { sel: '.two-col',                name: '03_allocation_signals'},
  { sel: '#backtest-grid',          name: '04_backtest'          },
  { sel: '#ticker-charts',          name: '05_positions'         },
  { sel: '#trades-table',           name: '06_trades'            },
  { sel: '.data-table:last-of-type',name: '07_suggestions'       },
];

// ── Helpers ───────────────────────────────────────────────────
function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

async function waitForCharts(page) {
  // Wait for Chart.js canvases to have rendered content
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
    // Call loadData which is exposed globally in app.js
    if (typeof loadData === 'function') {
      loadData(d);
    }
  }, data);
}

async function captureSection(page, sel, outPath) {
  try {
    const el = await page.$(sel);
    if (!el) {
      console.log(`  [skip] ${sel} not found`);
      return null;
    }
    // Scroll into view
    await el.scrollIntoViewIfNeeded();
    await new Promise(r => setTimeout(r, 300));

    // Add some padding around the element
    const box = await el.boundingBox();
    if (!box || box.width < 10 || box.height < 10) {
      console.log(`  [skip] ${sel} has no dimensions`);
      return null;
    }
    const clip = {
      x:      Math.max(0, box.x - 16),
      y:      Math.max(0, box.y - 16),
      width:  box.width  + 32,
      height: box.height + 32,
    };

    await page.screenshot({ path: outPath, clip, type: 'png' });
    console.log(`  ✅ ${path.basename(outPath)} (${Math.round(box.width)}×${Math.round(box.height)})`);
    return outPath;
  } catch (err) {
    console.log(`  ⚠️  ${sel}: ${err.message}`);
    return null;
  }
}

async function buildPdf(pngPaths, pdfPath) {
  // Use the 'pdfkit' npm package if available, otherwise use a simple image concatenation
  let PDFDocument;
  try {
    PDFDocument = require('pdfkit');
  } catch {
    try {
      const { execSync } = require('child_process');
      execSync('npm install pdfkit --save-dev', { cwd: path.dirname(__filename), stdio: 'inherit' });
      PDFDocument = require('pdfkit');
    } catch {
      console.log('  [pdf] pdfkit not available — skipping PDF compilation');
      return;
    }
  }

  const doc = new PDFDocument({ autoFirstPage: false, margin: 0 });
  const writeStream = fs.createWriteStream(pdfPath);
  doc.pipe(writeStream);

  for (const imgPath of pngPaths) {
    if (!imgPath || !fs.existsSync(imgPath)) continue;
    const { createCanvas, loadImage } = (() => { try { return require('canvas'); } catch { return {}; } })();

    // Get image dimensions without canvas dep — use pdfkit's built-in
    doc.addPage({ size: 'A4', layout: 'landscape', margin: 0 });
    try {
      doc.image(imgPath, 0, 0, {
        fit:   [doc.page.width, doc.page.height],
        align: 'center',
        valign: 'center',
      });
    } catch (e) {
      console.log(`  [pdf] Could not embed ${path.basename(imgPath)}: ${e.message}`);
    }
  }

  doc.end();
  await new Promise((res, rej) => {
    writeStream.on('finish', res);
    writeStream.on('error',  rej);
  });
  console.log(`  📄 PDF written: ${pdfPath}`);
}

// ── Main ──────────────────────────────────────────────────────
(async () => {
  console.log(`\n📸 Roboadvisor Dashboard Screenshots`);
  console.log(`   JSON:   ${jsonPath}`);
  console.log(`   Output: ${outDir}`);
  console.log(`   Width:  ${width}px\n`);

  if (!fs.existsSync(jsonPath)) {
    console.error(`ERROR: JSON file not found: ${jsonPath}`);
    process.exit(1);
  }

  ensureDir(outDir);

  const browser = await puppeteer.launch({
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', `--window-size=${width},900`],
  });

  const page = await browser.newPage();
  await page.setViewport({ width, height: 900, deviceScaleFactor: 2 }); // 2x for crisp retina-like output

  // Load the dashboard
  console.log(`Loading ${serverUrl} ...`);
  await page.goto(serverUrl, { waitUntil: 'networkidle0', timeout: 30000 });

  // Inject the JSON data
  console.log('Injecting dashboard data...');
  await injectData(page, jsonPath);

  // Wait for charts to finish rendering
  console.log('Waiting for charts to render...');
  await waitForCharts(page);

  // Capture each section
  console.log('\nCapturing sections:');
  const captured = [];
  for (const section of SECTIONS) {
    const outPath = path.join(outDir, `${section.name}.png`);
    const result  = await captureSection(page, section.sel, outPath);
    if (result) captured.push(result);
  }

  // Also capture the full dashboard as one long scroll
  const fullPath = path.join(outDir, '00_full_dashboard.png');
  await page.evaluate(() => window.scrollTo(0, 0));
  await new Promise(r => setTimeout(r, 300));
  await page.screenshot({ path: fullPath, fullPage: true, type: 'png' });
  console.log(`  ✅ 00_full_dashboard.png (full page)`);

  await browser.close();

  // Optional PDF compilation
  if (makePdf) {
    const pdfPath = path.join(outDir, 'report.pdf');
    console.log('\nCompiling PDF...');
    // Install pdfkit if needed
    try {
      require('pdfkit');
    } catch {
      const { execSync } = require('child_process');
      console.log('  Installing pdfkit...');
      execSync('npm install pdfkit', { cwd: path.dirname(path.resolve(__filename)), stdio: 'pipe' });
    }
    await buildPdf([fullPath, ...captured], pdfPath);
  }

  console.log(`\n✅ Done. ${captured.length + 1} screenshots in: ${outDir}\n`);
})();
