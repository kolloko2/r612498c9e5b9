'use strict';
// Derive guidance from received facts only. Never read the private marking rubric.
(function(root){
 // Готовые формулировки берутся из сведений этой карточки и полученных докладов,
 // а не из скрытого эталона: ученик видит, что и как записать.
 function facts(s){
  const c=s.card||{},crew=s.assigned_crew||(s.crew_options||[])[0]||{};
  const address=[c.city,c.street&&('ул. '+c.street),c.house&&('д. '+c.house)].filter(Boolean).join(', ')||c.address_note||'адрес из карточки';
  return {address,incident:c.incident_type||'происшествие',crew:crew.id||'бригада',leader:crew.leader||'старший бригады',
   injured:c.injured?'Есть пострадавшие':'Пострадавших нет'};
 }
 function nextAction(s){
  if(!s||s.exercise_mode!=='actions')return null;
  const events=s.events||[], status=s.service_states?.[s.owner_service]?.status, f=facts(s);
  if(s.status==='Завершена')return {title:'Занятие завершено',text:'Откройте отчёт: в разделе «Решения диспетчера» видно, какие действия выполнены и где ошибки. Кнопка «Получить ИИ-разбор» разберёт ваши записи с цитатами.',target:'audit'};
  if(!status||['Добавлена','Получена службой'].includes(status))return {title:'Шаг 1. Примите карточку',
   text:`В «Реагировании» службы «${s.owner_service}» выберите статус «Принята» и напишите комментарий — он обязателен, без него статус не сохранится. Когда ✓ станет зелёной, нажмите её. Первая запись — не позже 3 минут.`,
   sample:`Принято в работу. ${f.incident}, ${f.address}. Направляем бригаду.`,fill:'responseComment',target:'responseStatus'};
  if(status==='Не принята')return {title:'Проверьте обоснование отказа',text:'В комментарии должны быть причина и кому передана информация. Затем завершите карточку.',target:'finish'};
  if((s.crew_options||[]).length&&!s.assigned_crew&&!s.card_locked)return {title:'Шаг 2. Назначьте бригаду',
   text:`В блоке «Бригада» выберите «${f.crew} · ${f.leader}», в поле «Решение принял» оставьте «Диспетчер» и нажмите «Назначить». Номер наряда в статусе не заменяет назначение бригады.`,target:'crewSelect'};
  const briefs=events.filter(e=>e.type==='notification.recorded'&&e.detail?.source==='briefing');
  const crewBrief=briefs.some(e=>e.detail?.counterpart==='crew'||e.detail?.crew_id);
  const superiorBrief=briefs.some(e=>e.detail?.counterpart==='superior'||(!e.detail?.counterpart&&!e.detail?.crew_id));
  if(s.assigned_crew&&!crewBrief&&!s.card_locked)return {title:'Шаг 3. Передайте задачу бригаде',
   text:`Нажмите «Доложить по телефону», адресат — «${f.crew} · ${f.leader}», нажмите «Позвонить». Когда бригада ответит, ${s.sip_extension?'произнесите в телефоне пример ниже':'передайте текст ниже через текстовый доклад'}. После подтверждения в поле «Информацию принял» укажите «${f.leader}» и нажмите «Завершить доклад».`,
   sample:`${f.crew}, выезжайте: ${f.address}. ${f.incident}. ${f.injured}.`,fill:s.sip_extension||s.text_input_allowed===false?null:'briefingText',target:'openBriefing'};
  if(!superiorBrief&&!s.card_locked)return {title:`Шаг ${s.assigned_crew?'4':'3'}. Доложите начальнику дежурной смены`,
   text:`Вышестоящему начальнику докладывают, где и что произошло и кто направлен: так руководство знает обстановку. Нажмите «Доложить по телефону», адресат — «${s.owner_service} · начальник дежурной смены», нажмите «Позвонить» и ${s.sip_extension?'доложите голосом по примеру ниже':'передайте текст ниже'}. В поле «Информацию принял» укажите «Начальник дежурной смены» и нажмите «Завершить доклад». Без этого доклада карточку не завершить.`,
   sample:`Докладываю: ${f.address}, ${f.incident}. ${f.injured}. Направлена ${f.crew}, старший — ${f.leader}.`,fill:s.sip_extension||s.text_input_allowed===false?null:'briefingText',target:'openBriefing'};
  if((s.correction_evidence||[]).length&&!(s.error_reports||[]).length&&!s.card_locked)return {title:'Бригада сообщила сведения, отличные от карточки',
   text:`Сверьте с карточкой: «${s.correction_evidence[0]}». Поля карточки 112 не правятся: нажмите «Сообщить в 112 об ошибке», выберите поле с ошибкой, впишите правильное значение ровно как сказала бригада, источник — «${f.leader}», и кто принял сообщение в 112.`,target:'openErrorReport'};
  const update=events.find(e=>e.type==='situation.update'&&e.detail?.unlocks_status&&!events.some(a=>a.type==='service.updated'&&a.detail?.service===s.owner_service&&a.detail?.status===e.detail.unlocks_status&&a.seq>e.seq));
  if(update&&!s.card_locked){const next=update.detail.unlocks_status,last=next==='Работы завершены';
   return {title:`Отразите доклад: статус «${next}»`,
    text:`Бригада сообщила: «${update.detail.text}». Выберите статус «${next}», в «Комментарий службы» запишите своими словами всё существенное из доклада — что делают, что установили${last?', каков результат работ':''} — и нажмите ✓. Слова «ок», «готово» не засчитываются.${last?' После этого статуса редактирование закроется, поэтому запишите итог полностью.':''}`,
    sample:(last?'Работы завершены. ':'')+update.detail.text,fill:'responseComment',target:'responseComment'};}
  if(s.card_locked){
   if(!s.processed_at)return {title:'Отметьте отработку',text:'Итоговый статус и результат сохранены. Нажмите «Отметить отработанным»: это отметка по происшествию, ещё не завершение занятия.',target:'processed'};
   return {title:'Завершите карточку',text:'Нажмите «Завершить карточку». Если остались обязательные действия, они будут перечислены. Затем откройте отчёт и запросите ИИ-разбор.',target:'finish'};
  }
  if((s.pending_phone_reports||[]).length)return {title:'Примите доклад бригады',text:'Бригада звонит: ответьте в телефоне и выслушайте доклад до конца. В списке докладов нажмите «Подтвердить получение», затем обновите статус и запишите сведения в комментарий.',target:'situationFeed'};
  return {title:'Ждите доклада бригады',text:'Следующий доклад поступит сам, после того как предыдущий отражён статусом. Можно нажать «Уточнить ход работ». Не ставьте следующий статус заранее: после доклада о выезде — «Начало реагирования», о прибытии — «Прибытие», о начале работ — «Проведение работ».',target:'requestProgress'};
 }
 if(typeof module!=='undefined')module.exports={nextAction};
 if(!root.document)return;
 let state=null,enabled=false;
 const $=id=>document.getElementById(id);
 const panel=document.createElement('aside');panel.id='ddsCoach';panel.hidden=true;panel.setAttribute('aria-label','Практика диспетчера');
 const title=document.createElement('h3'),text=document.createElement('p'),go=document.createElement('button'),close=document.createElement('button');
 const sample=document.createElement('blockquote'),insert=document.createElement('button');
 go.type=close.type=insert.type='button';go.textContent='Показать, куда нажать';close.textContent='Скрыть подсказки';insert.textContent='Вставить текст';
 sample.className='dds-coach-sample';insert.className='dds-coach-insert';
 panel.append(title,text,sample,insert,go,close);document.body.append(panel);
 // Вставляет образец в нужное поле; если окно доклада ещё не открыто — подскажет.
 insert.onclick=()=>{const step=nextAction(state),field=step?.fill&&$(step.fill);
  if(!field||field.closest('dialog:not([open])')||field.closest('[hidden]')){insert.textContent='Сначала откройте окно, затем вставьте';setTimeout(()=>insert.textContent='Вставить текст',2500);return;}
  field.value=step.sample;field.dispatchEvent(new Event('input',{bubbles:true}));field.focus();};
 function key(){return 'ddsCoach:'+state?.id;}
 // Подсказка встаёт рядом с элементом, о котором говорит: над ним или под ним.
 function place(){
  if(panel.hidden)return;
  // Модальное окно (доклад, ошибка в 112) перекрывает всё остальное: подсказка
  // переезжает в само окно и встаёт сбоку от него, чтобы её можно было нажать.
  const dialog=document.querySelector('dialog[open]'),host=dialog||document.body;
  if(panel.parentElement!==host)host.append(panel);
  panel.style.width='';panel.style.maxHeight='';
  const step=nextAction(state),target=step&&$(step.target);
  // Не закрывать поля, которые нужно заполнить: якорь — вся панель реагирования или окно.
  const anchor=dialog||target?.closest('#responseSection, .response-section')||target,box=anchor?.getBoundingClientRect();
  if(!box||!box.width||!box.height){panel.style.left='';panel.style.top='';panel.style.right='';return;}
  let width=panel.offsetWidth;
  const right=innerWidth-box.right-24,left=box.left-24;
  let x,y;
  if(dialog){
   const room=Math.max(right,left);if(room<width&&room>=220){width=room;panel.style.width=room+'px';}
   x=right>=width?box.right+12:left>=width?box.left-width-12:innerWidth-width-10;y=box.top;
  }else{
   // Справа над панелью: слева адрес и описание — их ученик читает во время шага.
   x=Math.min(box.right-width,innerWidth-width-10);y=box.top-panel.offsetHeight-12;
   if(y<10){
    // Над панелью мало места: ниже — если помещается, иначе ужать и прокручивать.
    if(box.bottom+12+panel.offsetHeight<=innerHeight-10)y=box.bottom+12;
    else{panel.style.maxHeight=Math.max(160,box.top-22)+'px';y=10;}
   }
  }
  const height=panel.offsetHeight;
  y=Math.max(10,Math.min(y,innerHeight-height-10));x=Math.max(10,x);
  panel.style.right='auto';panel.style.left=x+'px';panel.style.top=y+'px';
 }
 function render(){
  const step=nextAction(state);panel.hidden=!state?.practice_with_hints||!enabled||!step||$('cardPanel')?.hidden;
  document.querySelectorAll('.dds-coach-target').forEach(n=>n.classList.remove('dds-coach-target'));
  if(panel.hidden)return;title.textContent=step.title;text.textContent=step.text;
  sample.hidden=!step.sample;insert.hidden=!step.sample||!step.fill;sample.textContent=step.sample?'Например: «'+step.sample+'»':'';
  requestAnimationFrame(place);
 }
 addEventListener('resize',()=>requestAnimationFrame(place));
 // Открытие и закрытие окон меняет место подсказки.
 new MutationObserver(()=>requestAnimationFrame(place)).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
 close.onclick=()=>{enabled=false;sessionStorage.setItem(key(),'off');render();};
 go.onclick=()=>{
  const step=nextAction(state);if(!step)return;
  if(['responseStatus','responseComment','crewSelect','openBriefing','requestProgress'].includes(step.target)){
   document.querySelectorAll('#services details[open]').forEach(d=>d.open=false);
   $('cardPanel').classList.remove('response-compact');$('cardPanel').classList.add('response-open');
  }
  const target=$(step.target);target?.scrollIntoView({block:'nearest'});target?.focus();target?.classList.add('dds-coach-target');
  requestAnimationFrame(place);
 };
 root.updateDdsCoach=s=>{const changed=state?.id!==s?.id;state=s;if(!s?.practice_with_hints)enabled=false;else if(changed){const saved=sessionStorage.getItem(key());enabled=saved!=='off';}render();};
 root.startDdsCoach=()=>{if(!state?.practice_with_hints)return;enabled=true;sessionStorage.setItem(key(),'on');render();};
})(typeof window==='undefined'?globalThis:window);
