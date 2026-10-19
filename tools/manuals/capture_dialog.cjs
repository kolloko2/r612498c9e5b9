// Снимок текстового разговора диспетчера с руководителем бригады — для быстрого старта.
// Проходит начало карточки от ИИ под «Слушатель 2»: принять, назначить бригаду, позвонить.
// Запуск: node tools/manuals/capture_dialog.cjs https://АДРЕС
const fs = require('fs');
const path = require('path');
const {chromium} = require('playwright');
const BASE = (process.argv[2] || 'https://212.22.78.93').replace(/\/$/, '');
const OUT = path.join(__dirname, '..', '..', 'docs', 'manuals', 'screens');
const marksFile = path.join(OUT, 'marks.json');
const marks = JSON.parse(fs.readFileSync(marksFile, 'utf8'));

async function box(locator) {
  const b = await locator.first().boundingBox().catch(() => null);
  return b && {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)};
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, deviceScaleFactor: 2, ignoreHTTPSErrors: true, locale: 'ru-RU'});
  await context.addInitScript(() => { try { localStorage.setItem('onboardingDone', '1'); localStorage.setItem('onboardingOffered', '1'); } catch {} });
  const page = await context.newPage();
  page.on('dialog', d => d.accept());
  await page.goto(BASE + '/login');
  await page.getByRole('button', {name: 'Выбрать: Слушатель 2'}).click();
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await page.waitForURL(/portal/);
  await page.getByRole('link', {name: 'Получить карточку от ИИ'}).click();
  await page.waitForTimeout(4000);
  if (await page.locator('#cardResponseTools').isVisible()) await page.locator('#cardResponseTools').click();
  await page.waitForTimeout(800);
  const status = page.locator('#responseStatus');
  if (await status.isVisible() && await status.locator('option', {hasText: 'Принята'}).count()) {
    await status.selectOption({label: 'Принята'});
    await page.locator('#responseComment').fill('Принята в работу, направляю бригаду.');
    await page.locator('#addResponse').click();
    await page.waitForTimeout(2000);
  }
  if (await page.locator('#assignCrew').isVisible() && await page.locator('#assignCrew').isEnabled()) {
    await page.locator('#assignCrew').click();
    await page.waitForTimeout(2000);
  }
  await page.locator('#openBriefing').click();
  await page.waitForTimeout(1500);
  const missing = await page.locator('#briefingMissing').textContent();
  console.log('окно телефона:', missing);
  // Без гарнитуры в стенде съёмки — запасной текстовый режим.
  if (await page.locator('#briefingTransport').isVisible()) await page.locator('#briefingTransport').selectOption('text');
  await page.locator('#briefingForm button[type=submit], #startBriefing').first().click();
  await page.waitForTimeout(2500);
  const address = await page.locator('#addressSummary').textContent().catch(() => '');
  await page.locator('#briefingText').fill(`Здравствуйте, диспетчер ДДС. По адресу ${address.trim()} поступила заявка. Бригаде нужен доступ: где ключи и кто встретит?`);
  await page.locator('#sendBriefing').click();
  await page.waitForFunction(() => document.querySelectorAll('#briefingTranscript .message, #briefingTranscript p, #briefingTranscript div').length >= 3, null, {timeout: 60000}).catch(() => {});
  await page.waitForTimeout(3000);
  console.log('разговор:', (await page.locator('#briefingTranscript').textContent()).slice(0, 400));
  const shot = path.join(OUT, 'student_dialog.png');
  await page.screenshot({path: shot});
  marks.student_dialog = {w: 1440, h: 900, marks: [
    {n: '1', ...(await box(page.locator('#briefingService')))},
    {n: '2', ...(await box(page.locator('#briefingTranscript')))},
    {n: '3', ...(await box(page.locator('#briefingText')))},
  ].filter(m => m.w)};
  fs.writeFileSync(marksFile, JSON.stringify(marks, null, 1));
  console.log('снят student_dialog');
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
