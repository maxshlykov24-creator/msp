#!/usr/bin/env node
// Export the offline HTML with embedded fonts/images; fail on missing assets or layout overflow.
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const {createRequire} = require('module');

function flags(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!argv[i].startsWith('--') || !argv[i + 1]) throw new Error('Каждому флагу нужно значение');
    out[argv[i].slice(2)] = argv[i + 1];
  }
  for (const key of ['html', 'pdf', 'qa-dir']) if (!out[key]) throw new Error(`Нужен --${key}`);
  return out;
}

(async () => {
  const args = flags(process.argv.slice(2));
  let playwright;
  if (args['node-modules']) {
    playwright = createRequire(path.join(path.resolve(args['node-modules']), '__runtime__.cjs'))('playwright');
  } else {
    try { playwright = require('playwright'); }
    catch { throw new Error('Найди пакет playwright через load_workspace_dependencies и передай --node-modules ПУТЬ'); }
  }
  const qa = path.resolve(args['qa-dir']);
  fs.mkdirSync(qa, {recursive: true});
  const pdf = path.resolve(args.pdf);
  fs.mkdirSync(path.dirname(pdf), {recursive: true});
  const browser = await playwright.chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1600, height: 900}, deviceScaleFactor: 1});
    const externalRequests = [];
    await page.route(/^https?:\/\//, route => {
      externalRequests.push(route.request().url().split('?')[0]);
      return route.abort();
    });
    await page.goto(pathToFileURL(path.resolve(args.html)).href);
    await page.emulateMedia({media: 'print'});
    await page.evaluate(async () => {
      await document.fonts.ready;
      await Promise.all([...document.images].map(img => img.decode()));
    });
    const checks = await page.evaluate(() => [...document.querySelectorAll('.slide')].map((slide, i) => {
      const sr = slide.getBoundingClientRect();
      const nodes = [...slide.querySelectorAll('main h1,main h2,main h3,main p,main blockquote,main figure')];
      const overflow = nodes.filter(e => {
        const r = e.getBoundingClientRect();
        return r.bottom > sr.bottom - 88 || r.right > sr.right - 60 || r.left < sr.left + 60 || r.top < sr.top + 100;
      }).map(e => ({tag: e.tagName, text: e.textContent.slice(0, 100)}));
      const overlaps = [];
      for (let a = 0; a < nodes.length; a++) for (let b = a + 1; b < nodes.length; b++) {
        const x = nodes[a], y = nodes[b];
        if (x.contains(y) || y.contains(x)) continue;
        const rx = x.getBoundingClientRect(), ry = y.getBoundingClientRect();
        if (Math.min(rx.right, ry.right) - Math.max(rx.left, ry.left) > 2 &&
            Math.min(rx.bottom, ry.bottom) - Math.max(rx.top, ry.top) > 2) {
          overlaps.push([x.textContent.slice(0, 50), y.textContent.slice(0, 50)]);
        }
      }
      const images = [...slide.querySelectorAll('img')].map(img => ({
        loaded: img.complete && img.naturalWidth > 0,
        background: img.classList.contains('texture'),
        width: img.naturalWidth, height: img.naturalHeight,
      }));
      return {slide: i + 1, overflow, overlaps, images};
    }));
    const fonts = await page.evaluate(() => [...document.fonts].map(f => ({family: f.family, style: f.style, weight: f.weight, status: f.status})));
    const report = {slides: checks, fonts, externalRequests};
    fs.writeFileSync(path.join(qa, 'layout-check.json'), JSON.stringify(report, null, 2));
    for (let i = 0; i < checks.length; i++) {
      await page.locator('.slide').nth(i).screenshot({path: path.join(qa, `slide-${String(i + 1).padStart(2, '0')}.png`)});
    }
    if (!checks.length || externalRequests.length || checks.some(x => x.overflow.length || x.overlaps.length || x.images.some(i => !i.loaded))) {
      throw new Error(`Проверка не пройдена. Открой ${path.join(qa, 'layout-check.json')} и PNG; исправь содержание/композицию, затем повтори экспорт.`);
    }
    await page.pdf({path: pdf, printBackground: true, preferCSSPageSize: true});
    console.log(JSON.stringify({pdf, slides: checks.length, qa, layout: 'passed', offline: true}));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
