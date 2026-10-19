// Предпросмотр: печатная вёрстка руководства как картинки (для проверки без PDF-просмотрщика).
// node tools/manuals/preview.cjs output/manuals/01_quick_start.html out.png
const {chromium} = require('playwright');
const path = require('path');
(async () => {
  const [src, out] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({viewport: {width: 794, height: 1123}, deviceScaleFactor: 1.5});
  await page.emulateMedia({media: 'print'});
  await page.goto('file:///' + path.resolve(src).replace(/\\/g, '/'), {waitUntil: 'networkidle'});
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({path: out, fullPage: true});
  await browser.close();
})();
