'use strict';
// Derive guidance from received facts only. Never read the private marking rubric.
(function(root){
 function nextAction(s){
  if(!s||s.exercise_mode!=='actions')return null;
  const events=s.events||[], status=s.service_states?.[s.owner_service]?.status;
  if(s.status==='Завершена')return {title:'Занятие завершено',text:'Откройте отчёт: в разделе «Решения диспетчера» показано, какие действия выполнены и где допущены ошибки.',target:'audit'};
  if(!status||['Добавлена','Получена службой'].includes(status))return {title:'1. Примите решение по карточке',text:'Прочитайте адрес и происшествие. В блоке своей службы выберите «Принята» либо обоснованный отказ и нажмите ✓. Нормативы: открыть карточку — 30 секунд, первая запись (статус и текст) — 3 минуты от поступления. Комментарий к статусу обязателен.',target:'responseStatus'};
  if(status==='Не принята')return {title:'Проверьте обоснование отказа',text:'В комментарии должны быть причина и сведения о передаче информации. Затем завершите карточку: правильность решения проверит оценка.',target:'finish'};
  if((s.crew_options||[]).length&&!s.assigned_crew&&!s.card_locked)return {title:'2. Организуйте реагирование',text:'Выберите бригаду своей службы, укажите, кто принял решение, и нажмите «Назначить». Номер наряда в статусе не заменяет назначение бригады.',target:'crewSelect'};
  const briefs=events.filter(e=>e.type==='notification.recorded'&&e.detail?.source==='briefing');
  const crewBrief=briefs.some(e=>e.detail?.counterpart==='crew'||e.detail?.crew_id);
  const superiorBrief=briefs.some(e=>e.detail?.counterpart==='superior'||(!e.detail?.counterpart&&!e.detail?.crew_id));
  if(s.assigned_crew&&!crewBrief&&!s.card_locked)return {title:'3. Передайте задачу бригаде',text:'Нажмите «Доложить по телефону», адресат — назначенная бригада. Передайте адрес, происшествие и сведения о пострадавших. Когда бригада подтвердит, укажите, кто принял информацию, и нажмите «Завершить доклад».',target:'openBriefing'};
  if(!superiorBrief&&!s.card_locked)return {title:`${s.assigned_crew?'3б':'3'}. Доложите вышестоящему начальнику`,text:`О происшествии и принятом решении диспетчер докладывает начальнику дежурной смены своей службы: так руководство знает обстановку и может усилить реагирование. Нажмите «Доложить по телефону», адресат — «${s.owner_service} · начальник дежурной смены». Назовите адрес, что произошло и какая бригада направлена.`,target:'openBriefing'};
  const update=events.find(e=>e.type==='situation.update'&&e.detail?.unlocks_status&&!events.some(a=>a.type==='service.updated'&&a.detail?.service===s.owner_service&&a.detail?.status===e.detail.unlocks_status&&a.seq>e.seq));
  if(update&&!s.card_locked)return {title:'4. Зафиксируйте доклад',text:`Получено: «${update.detail.text}». Выберите «${update.detail.unlocks_status}», внесите существенные сведения в «Комментарий службы» и нажмите ✓.${update.detail.unlocks_status==='Работы завершены'?' Сначала запишите результат: после сохранения итогового статуса редактирование закроется.':''}`,target:'responseComment'};
  if(s.card_locked){
   if(!s.processed_at)return {title:'5. Отметьте отработку',text:'Итоговый статус и результат сохранены. Нажмите «Отметить отработанным»: это отметка по происшествию, ещё не завершение занятия.',target:'processed'};
   return {title:'6. Получите оценку',text:'Нажмите «Завершить карточку». Если остались обязательные действия, они будут перечислены перед завершением.',target:'finish'};
  }
  if((s.pending_phone_reports||[]).length)return {title:'4. Примите доклад с места',text:'Ответьте в телефоне, выслушайте доклад до конца. В списке докладов нажмите «Подтвердить получение». Затем обновите статус своей службы и запишите существенные сведения в комментарий.',target:'situationFeed'};
  return {title:'4. Контролируйте ход работ',text:'Ждите доклада бригады или нажмите «Уточнить ход работ». Не ставьте следующий статус заранее. После доклада о выезде — «Начало реагирования», о прибытии — «Прибытие», о начале работ — «Проведение работ».',target:'requestProgress'};
 }
 if(typeof module!=='undefined')module.exports={nextAction};
 if(!root.document)return;
 let state=null,enabled=false;
 const $=id=>document.getElementById(id);
 const panel=document.createElement('aside');panel.id='ddsCoach';panel.hidden=true;panel.setAttribute('aria-label','Практика диспетчера');
 const title=document.createElement('h3'),text=document.createElement('p'),go=document.createElement('button'),close=document.createElement('button');
 go.type=close.type='button';go.textContent='Показать, куда нажать';close.textContent='Скрыть подсказки';
 panel.append(title,text,go,close);document.body.append(panel);
 function key(){return 'ddsCoach:'+state?.id;}
 // Подсказка встаёт рядом с элементом, о котором говорит: над ним или под ним.
 function place(){
  const step=nextAction(state),target=step&&$(step.target),box=target?.getBoundingClientRect();
  if(panel.hidden)return;
  if(!box||!box.width||!box.height){panel.style.left='';panel.style.top='';panel.style.right='';return;}
  const width=panel.offsetWidth,height=panel.offsetHeight;
  let top=box.top-height-12;if(top<40)top=box.bottom+12;
  top=Math.max(40,Math.min(top,innerHeight-height-10));
  const left=Math.max(10,Math.min(box.left,innerWidth-width-10));
  panel.style.right='auto';panel.style.left=left+'px';panel.style.top=top+'px';
 }
 function render(){
  const step=nextAction(state);panel.hidden=!enabled||!step||$('cardPanel')?.hidden;
  document.querySelectorAll('.dds-coach-target').forEach(n=>n.classList.remove('dds-coach-target'));
  if(panel.hidden)return;title.textContent=step.title;text.textContent=step.text;
  requestAnimationFrame(place);
 }
 addEventListener('resize',()=>requestAnimationFrame(place));
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
 root.updateDdsCoach=s=>{const changed=state?.id!==s?.id;state=s;if(changed){const saved=sessionStorage.getItem(key());enabled=saved==='on'||(!saved&&s?.scenario_id==='dds-guided-practice-v1');}render();};
 root.startDdsCoach=()=>{enabled=true;sessionStorage.setItem(key(),'on');render();};
})(typeof window==='undefined'?globalThis:window);
