// Проверка элементов рабочего места, воспроизводящих реальную Систему 112:
// номер АРМ с экрана входа, красный таймер при превышении норматива, горячие
// клавиши с подсказками по Alt, статус оператора в телефонии, подчёркивание
// основной службы, подтверждение оповещения, напоминание и закрытие
// нерезультативного вызова. Весь HTTP перехвачен: ни Backend, ни базы, ни сети.
const playwright = require('playwright/test'), { expect } = playwright;
const engine = playwright[process.env.BROWSER_ENGINE || 'chromium'];
if (!engine) throw Error('Unknown BROWSER_ENGINE ' + process.env.BROWSER_ENGINE);
const fs = require('node:fs/promises'), path = require('node:path');
const root = __dirname, origin = 'http://127.0.0.1:3000';
const now = new Date().toISOString();
const blank = {
  country: 'Россия', region: 'Москва', city: 'Москва', object: '', district: '', area: '',
  street: 'Берзарина', house: '21', building: '', structure: '', apartment: '', entrance: '',
  floor: '', code: '', address_note: '', caller_name: 'Учебный заявитель', caller_status: 'очевидец',
  phone: '', supplied_phone: '', scene_phone: '', description: 'Учебное сообщение',
  incident_type: 'пожар: квартира', classifier_group: '1',
  classifier_features: ['в доме', 'квартира', 'открытое пламя'],
  classifier_id: '1010101', classifier_version: 'test-v1', latitude: null, longitude: null,
  emergency: false, important: false, bookmarked: false, injured: false, refused: false,
  no_access: false, no_contact: false, interrupted: false, services: ['Служба 101', 'ЦОДД'],
};
const routing = {
  rules_version: 'full-v2', source_row: 5, classifier_id: '1010101', classifier_version: 'test-v1',
  flags: {}, primary_services: ['Служба 101'],
  suggestions: [
    { service: 'Служба 101', primary: true, mappings: [{ cell: 'N5', incident_type: 'пожар: квартира' }] },
    { service: 'ЦОДД', primary: false, mappings: [{ cell: 'BW5', incident_type: 'карточка-112' }] },
  ],
  excluded: [], unresolved: [], warnings: [],
};
let cards, reminders, unproductive;
function reset() {
  reminders = []; unproductive = [];
  cards = [
    // Сохранённая карточка: норматив 30 секунд заведомо превышен.
    {
      id: 'card-open', number: 910201, title: 'Пожар в квартире', scenario_id: 's1',
      created_at: '2026-09-15T09:00:00+00:00', saved_at: now, status: 'В работе',
      incident_status: 'Зарегистрирована', revision: 1, time_limit_seconds: 30,
      registration: { operator: 'Курсант Петров', workstation: 'АРМ-1', workstation_source: 'teacher' },
      card: { ...blank }, events: [],
      service_states: {
        'Служба 101': { status: 'Добавлена', added_at: now },
        // Служба, добавленная внешней системой, помечается ВИС.
        'ЦОДД': { status: 'Добавлена', added_at: now, source: 'vis' },
      },
      allowed_service_statuses: { 'Служба 101': ['Принята', 'Не принята'], 'ЦОДД': ['Принята', 'Не принята'] },
      notifications: [], linked_cards: [], reminders: [], routing, transport: 'text',
      messages: [], assessment_enabled: false,
    },
    // Новая незарегистрированная карточка: на ней проверяются подтверждение
    // оповещения и закрытие нерезультативного вызова.
    {
      id: 'card-new', number: 910202, title: 'Новое обращение', scenario_id: 's2',
      created_at: now, status: 'Новая', incident_status: 'Новая', revision: 0,
      time_limit_seconds: 180,
      registration: { operator: 'Курсант Петров', workstation: 'АРМ-1' },
      card: { ...blank, services: [], incident_type: '', classifier_id: '', classifier_features: [] },
      events: [], service_states: {}, allowed_service_statuses: {}, notifications: [],
      linked_cards: [],
      reminders: [{ message_id: 'rem-1', text: 'Перезвонить заявителю', at: '2026-09-15T09:00:00+00:00', created_at: now }],
      transport: 'text', messages: [], assessment_enabled: false,
    },
  ];
}
const copy = value => JSON.parse(JSON.stringify(value));
const card = id => cards.find(item => item.id === id);
async function serve(page) {
  await page.route('**/*', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname;
    if (url.origin !== origin) return route.abort();
    if (p.startsWith('/api/')) {
      let data;
      if (p === '/api/v1/auth/me') data = { id: 'student-test', role: 'student', display_name: 'Курсант Петров', active: true };
      else if (p === '/api/v1/student/softphone') data = { enabled: false, reason: 'Номер не назначен' };
      else if (p === '/api/v1/health') data = { status: 'ok', provider: 'ollama' };
      else if (p === '/api/v1/student/classifier') data = {
        version: 'test-v1', groups: [{ id: '1', title: 'Пожары и задымления' }],
        records: [{ id: '1010101', group_id: '1', features: ['в доме', 'квартира', 'открытое пламя'], incident_type: 'пожар: квартира', main_service: 'MCHS', additional_details: '', source_row: 5 }],
      };
      else if (p === '/api/v1/student/routing/catalog') data = { rules_version: 'full-v2', services: ['Служба 101', 'Служба 102', 'ЦОДД', 'Мослифт'], flags: [] };
      else if (p === '/api/v1/student/routing/preview') data = routing;
      else if (p === '/api/v1/student/inbox/poll') data = [];
      else if (p === '/api/v1/student/assignments' || p === '/api/v1/student/lessons') data = [];
      else if (p === '/api/v1/student/sessions') data = cards.map(copy);
      else {
        const match = p.match(/^\/api\/v1\/student\/sessions\/([^/]+)(?:\/(card|reminders|unproductive))?$/);
        if (!match) throw Error('Unexpected API ' + request.method() + ' ' + p);
        const item = card(match[1]);
        if (!item) throw Error('Unknown fixture card ' + match[1]);
        const action = match[2];
        if (action === 'card') {
          const body = request.postDataJSON();
          item.card = body.card; item.revision++; item.saved_at = now;
          item.status = 'В работе'; item.incident_status = 'Зарегистрирована';
        } else if (action === 'reminders') {
          const body = request.postDataJSON();
          reminders.push(body);
          item.reminders = [...item.reminders, { ...body, created_at: now }];
        } else if (action === 'unproductive') {
          const body = request.postDataJSON();
          unproductive.push(body);
          item.card[body.kind] = true; item.revision++; item.status = 'Завершена';
          item.incident_status = 'Завершена'; item.checked_by = { role: 'student' };
          item.finished_at = now;
          item.evaluation = { timing: { elapsed_seconds: 4, limit_seconds: 180, within_limit: true, response_seconds: 4, response_limit_seconds: 30, response_within_limit: true } };
        }
        data = copy(item);
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
    }
    const file = p === '/' ? 'student.html' : p.slice(1);
    if (file.includes('..')) throw Error('Unsafe test asset');
    const body = await fs.readFile(path.join(root, file));
    await route.fulfill({ body, contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html' });
  });
}
(async () => {
  const browser = await engine.launch({ headless: true, ...(process.env.LESSON_BROWSER_PATH ? { executablePath: process.env.LESSON_BROWSER_PATH } : {}) });
  try {
    reset();
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } }), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('dialog', dialog => dialog.accept());
    await serve(page);
    // Номер АРМ введён на экране входа и хранится в этом браузере.
    await page.addInitScript(() => localStorage.setItem('workstation', 'АРМ-9'));
    await page.goto(origin);
    await expect(page.locator('#operatorSeat')).toHaveText('Курсант Петров · АРМ АРМ-9');

    // Статус оператора в телефонии: свободен, пока карточка не открыта.
    await expect(page.locator('#phonePresence')).toHaveAttribute('data-state', 'available');

    await page.locator('tr[aria-label="Происшествие 910201"]').click();
    await expect(page.locator('#cardNumber')).toContainText('910201');
    // При открытой карточке линия занята — как в инструкции оператора.
    await expect(page.locator('#phonePresence')).toHaveAttribute('data-state', 'busy');
    // Режим виден в шапке и меняет рабочее пространство: в ДДС нет элементов
    // приёма вызова, потому что карточку передала Служба 112.
    await expect(page.locator('#modeNote')).toHaveText('Расширенный режим: приём вызова 112');

    // Номер рабочего места в карточке — назначенный преподавателем.
    await expect(page.locator('#cardSeat')).toHaveText('Курсант Петров · АРМ-1');

    // Норматив 30 секунд превышен: поле таймера красное.
    await expect(page.locator('.timer.overdue')).toBeVisible({ timeout: 4000 });
    const background = await page.locator('.timer').evaluate(node => getComputedStyle(node).backgroundColor);
    if (background === 'rgb(48, 54, 56)') throw Error('Overdue timer must not keep the normal background');
    // Под общим временем виден сам норматив и превышение, а не только цвет.
    await expect(page.locator('#timerNorm')).toContainText('просрочено +');

    // Служба от внешней системы помечена ВИС, обычная — нет.
    await expect(page.locator('#services .vis-badge')).toHaveCount(1);
    await expect(page.locator('#services details', { has: page.locator('.vis-badge') })).toContainText('ЦОДД');

    // Основная служба подчёркнута двойной линией, остальные — нет.
    await expect(page.locator('#services .primary-service')).toHaveText('Служба 101');
    await expect(page.locator('#services .primary-service')).toHaveCount(1);

    // Горячие клавиши: Alt+O уводит фокус в описание, Alt+T — в тип происшествия.
    await page.locator('#edit').click();
    await page.keyboard.press('Alt+o');
    await expect(page.locator('#description')).toBeFocused();
    await page.keyboard.press('Alt+t');
    await expect(page.locator('#incidentType')).toBeFocused();
    // Удержание Alt показывает подсказки прямо на карточке.
    await page.keyboard.down('Alt');
    await expect(page.locator('.hotkey-hint').first()).toBeVisible();
    await page.keyboard.up('Alt');
    await expect(page.locator('.hotkey-hint')).toHaveCount(0);

    // Alt+Z открывает список оповещения с поиском и пометками служб. Пометки
    // приходят из свежего подбора, который запускается при открытии диалога.
    await page.keyboard.press('Alt+z');
    await expect(page.locator('#serviceDialog')).toBeVisible();
    await expect(page.locator('#routingPreview')).toContainText('строка 5');
    await expect(page.locator('#serviceChoices label.primary')).toContainText('Служба 101');
    await expect(page.locator('#serviceChoices label.auto')).toHaveCount(2);
    await page.locator('#serviceSearch').fill('мослифт');
    await expect(page.locator('#serviceChoices label')).toHaveCount(1);
    await page.locator('#serviceSearch').fill('');
    await page.locator('#serviceDialog button[data-close]').first().click();

    // Напоминание по карточке сохраняется отдельным запросом.
    await page.locator('#openReminder').click();
    await page.locator('#reminderText').fill('Перезвонить заявителю');
    await page.locator('#reminderMinutes').fill('7');
    await page.locator('#reminderForm button').click();
    await expect(page.locator('#reminderList')).toContainText('Перезвонить заявителю');
    if (reminders.length !== 1 || reminders[0].text !== 'Перезвонить заявителю') throw Error('Reminder was not submitted');
    await page.locator('#reminderDialog button[data-close]').click();

    // Новая карточка: сохранение спрашивает подтверждение оповещения служб.
    await page.locator('#journal').evaluate(button => button.click());
    await page.locator('tr[aria-label="Происшествие 910202"]').click();
    await expect(page.locator('#cardNumber')).toContainText('910202');
    await page.locator('#incidentType').selectOption('1');
    for (const value of ['в доме', 'квартира', 'открытое пламя']) {
      await page.locator('#surveyOptions button', { hasText: value }).first().click();
    }
    await expect(page.locator('#services')).toContainText('Служба 101');
    await page.locator('#save').click();
    await expect(page.locator('#notifyDialog')).toBeVisible();
    await expect(page.locator('#notifyServices')).toContainText('Служба 101');
    await expect(page.locator('#notifyServices .primary-service')).toHaveCount(1);
    await page.locator('#confirmNotify').click();
    await expect(page.locator('#saveState')).toHaveText('Сохранено');
    if (card('card-new').revision !== 1) throw Error('Card was not saved after confirmation');

    // Опросная карта подписана вопросами, а не номерами признаков.
    await expect(page.locator('#surveyOptions')).toContainText('Где произошло');

    // Нерезультативный вызов закрывает карточку после подтверждения.
    reset();
    await page.goto(origin);
    await page.locator('tr[aria-label="Происшествие 910202"]').click();
    await page.locator('.contact-flags label', { hasText: 'нет контакта' }).click();
    await expect(page.locator('#unproductiveDialog')).toBeVisible();
    await expect(page.locator('#unproductiveTitle')).toHaveText('Нет контакта');
    await page.locator('#confirmUnproductive').click();
    await expect(page.locator('#incidentState')).toContainText('Завершена');
    if (unproductive.length !== 1 || unproductive[0].kind !== 'no_contact') throw Error('Unproductive call was not reported');

    // Карточка со службами нерезультативной не считается: флажок её не закрывает.
    await page.locator('#journal').evaluate(button => button.click());
    await page.locator('tr[aria-label="Происшествие 910201"]').click();
    await page.locator('#edit').click();
    await page.locator('.contact-flags label', { hasText: 'срыв звонка' }).click();
    await expect(page.locator('#unproductiveDialog')).toBeHidden();
    await expect(page.locator('#notice')).toContainText('не считается нерезультативным');

    // Журнал сужается до текущего занятия: после смены занятия чужие карточки
    // мешают, а по умолчанию журнал накапливает всю смену, как в АРМ.
    await expect(page.locator('tr[aria-label^="Происшествие"]')).toHaveCount(2);
    // Панель карточки перекрывает тулбар журнала, поэтому клик программный.
    await page.locator('#lessonScope').evaluate(box => { box.checked = true; box.dispatchEvent(new Event('change')); });
    // Занятие не выбрано — флажок ничего не прячет.
    await expect(page.locator('tr[aria-label^="Происшествие"]')).toHaveCount(2);
    await page.locator('#lessonScope').evaluate(box => { box.checked = false; box.dispatchEvent(new Event('change')); });

    // Напоминание по отложенной карточке всплывает при закрытой карточке:
    // именно так оно описано в инструкции оператора.
    reset();
    await page.goto(origin);
    await expect(page.locator('#reminderAlert')).toBeVisible({ timeout: 12000 });
    await expect(page.locator('#reminderAlertText')).toHaveText('Перезвонить заявителю');
    await expect(page.locator('#reminderAlertCard')).toContainText('910202');
    await page.locator('#openReminderCard').click();
    await expect(page.locator('#cardNumber')).toContainText('910202');

    if (errors.length) throw Error(errors.join('\n'));
    console.log('ARM realism browser flow (' + (process.env.BROWSER_ENGINE || 'chromium') + '): PASS (mock HTTP, no Backend)');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
