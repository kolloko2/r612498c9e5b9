'use strict';
const el=id=>document.getElementById(id);
let catalog=null,current=null,busy=false;
const difficultyNames={basic:'Базовый',standard:'Стандартный',advanced:'Повышенный'};
const categoryNames={fire:'Пожары',traffic:'ДТП и транспорт',medical:'Медицина',utilities:'ЖКХ и инженерные сети',public:'Общественная безопасность',other:'Прочие'};
function status(text,error=false){el('status').textContent=text;el('status').className=error?'error':'';}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
async function api(path,method='GET',body){
 const r=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Voice-UI':'1'},...(body===undefined?{}:{body:JSON.stringify(body)})});
 if(r.status===401)location.replace('/login');
 if(r.status===403)location.replace('/portal');
 const data=await r.json();
 if(!r.ok)throw Error(typeof data.detail==='string'?data.detail:'Не удалось выполнить запрос');
 return data;}
async function run(fn,done){if(busy)return;busy=true;lock();try{await fn();if(done)status(done);}catch(e){status(e.message,true);}finally{busy=false;lock();}}
function lock(){el('publish').disabled=busy||!current||!selected().length;}
function selected(){return [...document.querySelectorAll('input[data-draft]:checked')].map(i=>i.dataset.draft);}

function renderList(){
 const list=el('ticketList');list.replaceChildren();
 for(const ticket of catalog.tickets){
  const item=node('li'),button=node('button',`Билет ${ticket.number}`);
  button.type='button';
  const done=ticket.calls.filter(call=>call.published_scenario_id).length;
  button.append(node('span',done?` · опубликовано ${done} из ${ticket.calls.length}`:` · ${ticket.calls.length} вызова`,'muted'));
  if(current&&current.number===ticket.number)button.classList.add('active');
  button.onclick=()=>run(()=>open(ticket.number));
  item.append(button);list.append(item);}
}

function renderDetail(){
 el('detailTitle').textContent=`Билет ${current.number} · страница источника ${current.page}`;
 const root=el('calls');root.replaceChildren();
 for(const call of current.calls){
  const box=node('article',undefined,'call'),scenario=call.scenario;
  const head=node('div',undefined,'call-head');
  const label=node('label',undefined,'pick');
  const check=document.createElement('input');check.type='checkbox';check.dataset.draft=call.id;
  check.disabled=!!call.published_scenario_id;check.onchange=lock;
  label.append(check,node('span',`Вызов ${call.call}: ${scenario.title}`));
  head.append(label,node('span',`${categoryNames[call.category_id]||call.category_id} · ${difficultyNames[call.difficulty]||call.difficulty}`,'badge'));
  box.append(head);
  if(call.published_scenario_id)box.append(node('p','Уже опубликован как сценарий '+call.published_scenario_id,'muted'));
  box.append(node('p',scenario.incident));
  box.append(node('p','Адрес: '+scenario.location,'muted'));
  const facts=node('ul',undefined,'facts');
  for(const fact of scenario.known_facts)facts.append(node('li',fact));
  box.append(node('h4','Известно заявителю'),facts);
  if(scenario.unknown_facts.length){
   const unknown=node('ul',undefined,'facts');
   for(const fact of scenario.unknown_facts)unknown.append(node('li',fact));
   box.append(node('h4','Заявителю неизвестно'),unknown);}
  const criteria=node('ul',undefined,'facts');
  for(const criterion of call.rubric.criteria)criteria.append(node('li',`${criterion.label}: ${criterion.expected.join(' / ')}`));
  box.append(node('h4',`Черновик эталона · норматив ${call.rubric.time_limit_seconds} с`),criteria);
  root.append(box);}
 lock();
}

async function open(number){
 current=await api('/api/v1/instructor/tickets/'+number);
 renderList();renderDetail();
 status(`Билет ${number} открыт. Отметьте вызовы для публикации.`);
}

el('publish').onclick=()=>run(async()=>{
 const ids=selected();
 if(!ids.length)throw Error('Отметьте хотя бы один вызов');
 const result=await api('/api/v1/instructor/tickets/publish','POST',{draft_ids:ids});
 const created=result.published.filter(item=>item.created).length;
 catalog=await api('/api/v1/instructor/tickets');
 await open(current.number);
 status(created?`Создано сценариев: ${created}. Проверьте и при необходимости отредактируйте их в библиотеке.`
              :'Эти вызовы уже были опубликованы ранее; повторные сценарии не создавались.');
});

run(async()=>{
 catalog=await api('/api/v1/instructor/tickets');
 const source=catalog.source||{};
 el('provenance').textContent=`Источник: ${source.document||'учебные билеты'} · билетов ${catalog.metadata?.tickets??0}, заготовок ${catalog.metadata?.drafts??0} · транскрипция ${source.transcription==='manual'?'ручная со сканов':'—'}.`;
 renderList();
},'Выберите билет слева');
