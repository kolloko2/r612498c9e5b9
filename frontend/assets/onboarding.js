'use strict';
// Режим новичка: пошаговое объяснение рабочего места, чтобы человек сам мог
// работать. Тур ничего не отправляет на сервер и не изменяет
// карточку — он только подсвечивает элементы и поясняет их назначение.
(function () {
 const STEPS = [
  {target: '#journalPanel', title: 'Список происшествий',
   text: 'Сюда поступают карточки. Строка — одно происшествие: номер, время, тип, адрес и статус. Щелчок по строке открывает карточку.'},
  {target: '#create', title: 'Новая карточка',
   text: 'Для полного цикла 112 карточку можно создать вручную. В занятии ДДС карточки поступают от Службы 112 в журнал.'},
  {target: '#responseSection', title: 'Решение о приёме',
   text: 'За 30 секунд от поступления карточки примите её или обоснованно откажите за свою ДДС. Одного открытия недостаточно. Карандаш своей службы открывает выбор статуса и комментарий.'},
  {target: '[data-field="caller_name"]', title: 'Заявитель',
   text: 'ФИО и статус заявителя: очевидец, пострадавший, родственник. Записывайте так, как человек представился.'},
  {target: '[data-field="street"], .address-fields', title: 'Адрес — самое важное поле',
   text: 'Улицу и дом переписывайте дословно и переспрашивайте при сомнении. Ошибка в одной букве отправит службы по другому адресу: Дубнинская и Дубининская — разные улицы.'},
  {target: '#description', title: 'Описание происшествия',
   text: 'Кратко и по существу: что произошло, есть ли пострадавшие, есть ли доступ. Это описание читает следующий диспетчер.'},
  {target: '#services', title: 'Службы оповещения',
   text: 'В готовой карточке службы уже назначены. Вы меняете статус только своей ДДС; состояние остальных служб доступно для просмотра.'},
  {target: '#save', title: 'Сохранение',
   text: 'Сохраняет карточку и открывает её в режиме просмотра. Чтобы дополнить сведения, нажмите «дополнение».'},
  {target: '#openBriefing', title: 'Доклад по телефону',
   text: 'Вызов дежурного службы: назовите адрес и тип происшествия. Дежурный подтвердит приём, а доклад запишется телефонограммой.'},
  {target: '#situationFeed', title: 'Вводные с места',
   text: 'Дождитесь сообщения от реагирующей стороны. Только после него появляется соответствующий статус хода работ; при нескольких карточках следите за каждой.'},
  {target: '#processed', title: 'Происшествие отработано',
   text: 'Отмечает, что вы выполнили действия по происшествию. Занятие при этом не завершается.'},
  {target: '#finish', title: 'Завершение занятия',
   text: 'Фиксирует результат и передаёт карточку на оценку. После этого изменения недоступны.'},
  {target: '#timer', title: 'Нормативы времени',
   text: 'За 30 секунд подтвердите получение карточки статусом «Принята» или «Не принята». Лимит обработки зависит от задания и расписания докладов; он указан в подсказке таймера. У каждой карточки свой отсчёт.'},
 ];
 const KEY = 'onboardingDone';
 let index = 0, overlay = null, box = null, active = false;

 const el = id => document.getElementById(id);
 function stored(value) {
  try { return value === undefined ? localStorage.getItem(KEY) : localStorage.setItem(KEY, value); }
  catch { return null; }
 }

 function place(step) {
  if(['#responseSection','#openBriefing'].includes(step.target)){
   const card=el('cardPanel');if(card?.classList.contains('dds-mode'))card.classList.add('response-open');
  }
  const node = document.querySelector(step.target);
  const spot = overlay.querySelector('.onboarding-spot');
  if (node && node.offsetParent !== null) {
   const rect = node.getBoundingClientRect();
   spot.hidden = false;
   spot.style.cssText = `top:${rect.top - 6}px;left:${rect.left - 6}px;` +
    `width:${rect.width + 12}px;height:${rect.height + 12}px`;
   const below = rect.bottom + 12;
   box.style.top = (below + 190 > window.innerHeight ? Math.max(12, rect.top - 200) : below) + 'px';
   box.style.left = Math.min(Math.max(12, rect.left), window.innerWidth - 360) + 'px';
  } else {
   // Элемент скрыт, пока карточка не открыта: поясняем шаг без подсветки.
   spot.hidden = true;
   box.style.top = '120px';
   box.style.left = Math.max(12, (window.innerWidth - 360) / 2) + 'px';
  }
 }

 function render() {
  const step = STEPS[index];
  box.querySelector('.onboarding-step').textContent = `Шаг ${index + 1} из ${STEPS.length}`;
  box.querySelector('h3').textContent = step.title;
  box.querySelector('p').textContent = step.text;
  box.querySelector('[data-onboarding="back"]').disabled = index === 0;
  box.querySelector('[data-onboarding="next"]').textContent =
   index === STEPS.length - 1 ? 'Завершить' : 'Далее';
  place(step);
 }

 // Крупная кнопка нужна до первого знакомства; дальше — маленькая «Обучение» справа.
 // Крупная кнопка — только при первом заходе: со следующего она маленькая,
 // даже если тур тогда не открывали (при карточках в журнале он не всплывает).
 const OFFERED = 'onboardingOffered';
 let offeredBefore = false;
 try { offeredBefore = !!localStorage.getItem(OFFERED); localStorage.setItem(OFFERED, '1'); } catch {}
 function compactButton() {
  const button = el('startTraining');
  if (!button || !(stored() || offeredBefore)) return;
  button.textContent = 'Обучение';
  button.classList.add('compact');
 }

 function stop() {
  active = false;
  stored('1');
  compactButton();
  overlay?.remove();
  overlay = box = null;
  window.removeEventListener('resize', reposition);
 }

 function reposition() { if (active) place(STEPS[index]); }

 function start(from = 0) {
  if (active) return;
  active = true; index = from;
  overlay = document.createElement('div');
  overlay.className = 'onboarding';
  overlay.innerHTML =
   '<div class="onboarding-spot" hidden></div>' +
   '<section class="onboarding-box" role="dialog" aria-label="Обучение работе с АРМ">' +
   '<span class="onboarding-step"></span><h3></h3><p></p>' +
   '<div class="onboarding-actions">' +
   '<button type="button" data-onboarding="back">Назад</button>' +
   '<button type="button" data-onboarding="next" class="primary">Далее</button>' +
   '<button type="button" data-onboarding="stop">Закрыть</button>' +
   '</div></section>';
  document.body.append(overlay);
  box = overlay.querySelector('.onboarding-box');
  overlay.addEventListener('click', event => {
   const action = event.target.dataset?.onboarding;
   if (!action) return;
   if (action === 'stop') return stop();
   if (action === 'back' && index > 0) index -= 1;
   else if (action === 'next') {
    if (index === STEPS.length - 1) return stop();
    index += 1;
   }
   render();
  });
  window.addEventListener('resize', reposition);
  render();
 }

 document.addEventListener('keydown', event => { if (active && event.key === 'Escape') stop(); });
 window.startOnboarding = start;

 // «Делай как я»: преподаватель переключает шаг, и он появляется у всех сразу.
 // Пока показ идёт, обучающийся не перематывает тур сам.
 let guided = null;
 window.applyGuidedStep = function (step) {
  if (step === guided) return;
  guided = step;
  if (step === null || step === undefined) {
   if (active && box) box.classList.remove('guided');
   return stop();
  }
  const target = Math.min(Math.max(1, step), STEPS.length) - 1;
  if (!active) start(target); else { index = target; render(); }
  box.classList.add('guided');
  box.querySelector('[data-onboarding="back"]').disabled = true;
  box.querySelector('[data-onboarding="next"]').disabled = true;
  box.querySelector('.onboarding-step').textContent =
   `Показывает преподаватель · шаг ${target + 1} из ${STEPS.length}`;
 };
 document.addEventListener('DOMContentLoaded', () => {
  el('onboarding')?.addEventListener('click', () => start(0));
  el('startTraining')?.addEventListener('click', () => start(0));
  compactButton();
  // Первый вход показываем сразу, но только на пустом рабочем месте: если
  // происшествия уже поступили, обучение не должно закрывать собой работу.
  if (!stored()) setTimeout(() => {
   if (!document.querySelector('tr[aria-label^="Происшествие"]')) start(0);
  }, 600);
 });
})();
