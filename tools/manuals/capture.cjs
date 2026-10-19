// Снимает экраны для руководств с живого стенда и запоминает положение
// элементов: по нему сборщик ставит нумерованные указатели.
// Запуск: node tools/manuals/capture.cjs https://АДРЕС
// Логины берутся из демонстрационной панели страницы входа.
const fs = require('fs');
const path = require('path');
const {chromium} = require('playwright');

const BASE = (process.argv[2] || 'https://212.22.78.93').replace(/\/$/, '');
const OUT = path.join(__dirname, '..', '..', 'docs', 'manuals', 'screens');
const only = new Set(process.argv.slice(3));
const marks = fs.existsSync(path.join(OUT, 'marks.json')) ? JSON.parse(fs.readFileSync(path.join(OUT, 'marks.json'), 'utf8')) : {};

async function box(locator) {
  try {
    const first = locator.first();
    if (!(await first.isVisible())) return null;
    const b = await first.boundingBox();
    return b && {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)};
  } catch { return null; }
}

async function shot(page, name, points = []) {
  if (only.size && !only.has(name)) return;
  await page.waitForTimeout(600);
  const found = [];
  for (const [n, locator] of points) {
    const b = await box(locator);
    if (b) found.push({n, ...b}); else console.warn(`  ${name}: указатель ${n} не найден`);
  }
  const vp = page.viewportSize();
  await page.screenshot({path: path.join(OUT, name + '.png')});
  marks[name] = {w: vp.width, h: vp.height, marks: found};
  console.log('снят', name, found.length ? `(${found.length} указ.)` : '');
}

async function login(page, role) {
  await page.goto(BASE + '/login');
  await page.getByRole('button', {name: 'Выбрать: ' + role}).click();
  await page.getByRole('button', {name: 'Войти', exact: true}).click();
  await page.waitForURL(/portal/);
  await page.waitForTimeout(1200);
}

(async () => {
  fs.mkdirSync(OUT, {recursive: true});
  const browser = await chromium.launch();
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, deviceScaleFactor: 2, ignoreHTTPSErrors: true, locale: 'ru-RU'});
  const page = await context.newPage();
  // Тур при первом входе закрывает экран: отмечаем его пройденным, кроме отдельного снимка.
  await context.addInitScript(() => { try { localStorage.setItem('onboardingDone', '1'); localStorage.setItem('onboardingOffered', '1'); } catch {} });

  // --- Вход ---
  await page.goto(BASE + '/login');
  await shot(page, 'login', [
    ['1', page.locator('main section, main [role=region]').first()],
    ['2', page.locator('#demoAccess')],
  ]);

  // --- Обучаемый ---
  await login(page, 'Слушатель 2');
  await shot(page, 'student_portal', [
    ['1', page.locator('.card-ai')],
    ['2', page.getByRole('link', {name: 'Рабочее место'})],
    ['3', page.getByRole('link', {name: 'Мои результаты'})],
  ]);
  await page.goto(BASE + '/');
  await page.waitForTimeout(1200);
  await shot(page, 'student_journal', [
    ['1', page.locator('tr[aria-label^="Происшествие"]')],
    ['2', page.locator('#create')],
    ['3', page.locator('nav[aria-label="Рабочее пространство"]')],
  ]);
  await page.locator('#create').click();
  await page.waitForTimeout(1500);
  await shot(page, 'student_new', [
    ['1', page.locator('#aiPracticeBox')],
    ['2', page.locator('#scenario')],
  ]);
  await page.keyboard.press('Escape');
  await page.locator('#onboarding').click();
  await page.waitForTimeout(800);
  await shot(page, 'student_tour', [['1', page.locator('.onboarding-box')]]);
  await page.getByRole('button', {name: 'Закрыть'}).last().click().catch(() => {});
  await page.keyboard.press('Escape');
  await page.goto(BASE + '/portal');
  await page.waitForTimeout(1200);
  await page.getByRole('link', {name: 'Получить карточку от ИИ'}).click();
  await page.waitForTimeout(3500);
  await shot(page, 'student_card', [
    ['1', page.locator('.card-ident')],
    ['2', page.locator('#timer')],
    ['3', page.locator('#addressSummary')],
    ['4', page.locator('.card-footer, .services').first()],
    ['5', page.locator('#cardResponseTools')],
  ]);
  const react = page.locator('#cardResponseTools');
  if (await react.isVisible()) await react.click();
  await page.waitForTimeout(800);
  await shot(page, 'student_response', [
    ['1', page.locator('#responseStatus')],
    ['2', page.locator('#responseComment')],
    ['3', page.locator('#crewSelect')],
    ['4', page.locator('#openBriefing')],
  ]);
  if (await page.locator('#openBriefing').isVisible()) {
    await page.locator('#openBriefing').click();
    await page.waitForTimeout(1500);
    const dialog = page.locator('dialog[open]');
    await shot(page, 'student_phone', [
      ['1', dialog.locator('ul, .contacts, .phone-contacts, [class*=contact]').first()],
      ['2', dialog.locator('input').first()],
    ]);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  }
  const soft = page.locator('#softphoneToggle');
  if (await soft.isVisible()) {
    await soft.click();
    await page.waitForTimeout(1500);
    await shot(page, 'student_softphone', [
      ['1', page.locator('#softphoneToggle')],
      ['2', page.locator('#softphoneDialog')],
    ]);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);
  }

  // --- Результат попытки (готовая завершённая карточка демонстрационного прохода) ---
  const SESSION = process.env.MANUAL_SESSION;
  if (SESSION) {
    await login(page, 'Слушатель 1');
    await page.goto(BASE + '/review?session=' + SESSION);
    await page.waitForTimeout(4000);
    await shot(page, 'student_result', [
      ['1', page.locator('h1, .score, [class*=score]').first()],
      ['2', page.locator('table, .checks, [class*=check]').first()],
    ]);
    await page.evaluate(() => window.scrollBy(0, 800));
    await shot(page, 'student_result_2', []);
    await page.locator('h2:has-text("Разбор"), h3:has-text("Разбор")').last().scrollIntoViewIfNeeded().catch(() => {});
    await page.evaluate(() => window.scrollBy(0, -120));
    await page.waitForTimeout(1500);
    await shot(page, 'student_result_review', [
      ['1', page.locator('.score').first()],
    ]);
    // Рекомендации ИИ: сам разбор в конце страницы.
    await page.evaluate(() => {
      const h = [...document.querySelectorAll('h3')].find(e => e.textContent.includes('ИИ-разбор'));
      if (h) { h.scrollIntoView({block: 'start'}); window.scrollBy(0, -110); }
    });
    await page.waitForTimeout(800);
    await shot(page, 'student_result_ai', [
      ['1', page.locator('h3:has-text("ИИ-разбор")').first()],
    ]);
    await page.goto(BASE + '/assessment');
    await page.waitForTimeout(3000);
    await shot(page, 'student_assessment', []);
  }

  // --- Преподаватель ---
  await login(page, 'Преподаватель');
  await shot(page, 'teacher_portal', [
    ['1', page.getByRole('link', {name: 'ИИ: создать карточку'})],
    ['2', page.locator('.lesson-builder')],
    ['3', page.locator('.more-menu>summary')],
  ]);
  await page.locator('#lessonForm').scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, -80));
  await shot(page, 'teacher_lesson_form', [
    ['1', page.locator('.ai-cards-toggle')],
    ['2', page.locator('#lessonGroup')],
    ['3', page.locator('#lessonCount')],
    ['4', page.locator('#lessonTransport')],
    ['5', page.locator('#lessonMore>summary')],
  ]);
  await page.evaluate(() => { document.querySelector('#lessons').scrollIntoView({block: 'start'}); window.scrollBy(0, -20); });
  await shot(page, 'teacher_lessons', [
    ['1', page.locator('.guided-control')],
    ['2', page.getByRole('button', {name: 'Участники в реальном времени'})],
    ['3', page.getByRole('button', {name: 'Отчёт и участники'})],
  ]);
  await page.goto(BASE + '/generation');
  await page.waitForTimeout(1500);
  await shot(page, 'teacher_generation', [
    ['1', page.locator('#category')],
    ['2', page.locator('#brief')],
    ['3', page.getByRole('button', {name: 'Создать упражнение целиком'})],
  ]);
  await page.goto(BASE + '/scenarios');
  await page.waitForTimeout(2000);
  await shot(page, 'teacher_scenarios', []);
  await page.goto(BASE + '/assessment');
  await page.waitForTimeout(2500);
  await shot(page, 'teacher_assessment', []);
  await page.evaluate(() => window.scrollBy(0, 900));
  await shot(page, 'teacher_assessment_2', []);
  if (SESSION) {
    await page.goto(BASE + '/review?session=' + SESSION);
    await page.waitForTimeout(4000);
    await shot(page, 'teacher_review', []);
  }

  // --- Администратор ---
  await login(page, 'Администратор');
  await shot(page, 'admin_portal', []);
  await page.goto(BASE + '/operations');
  await page.waitForTimeout(2500);
  await shot(page, 'admin_operations', [
    ['1', page.locator('#overallState')],
    ['2', page.locator('#services')],
    ['3', page.locator('.ops-more>summary')],
  ]);
  await page.locator('.ops-more>summary').click();
  await page.locator('.ops-more').scrollIntoViewIfNeeded();
  await page.evaluate(() => { document.querySelector('.ops-more').scrollIntoView({block: 'start'}); window.scrollBy(0, -20); });
  await shot(page, 'admin_operations_2', []);

  fs.writeFileSync(path.join(OUT, 'marks.json'), JSON.stringify(marks, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
