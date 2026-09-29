'use strict';
// Сквозной разбор попытки: задание → карточка → звонок → решение → разбор.
// Страница только показывает сохранённые результаты; оценку не пересчитывает.
const $=id=>document.getElementById(id);
function node(tag,text,className){const x=document.createElement(tag);if(text!==undefined&&text!==null)x.textContent=String(text);if(className)x.className=className;return x;}
async function api(path){const response=await fetch(path,{headers:{'X-Voice-UI':'1'}});const data=await response.json().catch(()=>null);if(!response.ok)throw Error(typeof data?.detail==='string'?data.detail:'Не удалось загрузить разбор');return data;}
const clock=seconds=>seconds===null||seconds===undefined?'—':`+${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`;
const span=seconds=>{if(seconds===null||seconds===undefined)return '—';const m=Math.floor(seconds/60),s=seconds%60;return m?`${m} мин ${s} с`:`${s} с`;};
const date=value=>value?new Date(value).toLocaleString('ru-RU'):'—';
const show=value=>value===null||value===undefined||value===''?'—':Array.isArray(value)?(value.length?value.join(', '):'—'):typeof value==='boolean'?(value?'да':'нет'):String(value);
function mark(passed){return passed===true?node('span','Выполнено','status good'):passed===false?node('span','Не выполнено','status critical'):node('span','Не измерено','status neutral');}
function pairs(rows){const list=node('dl',undefined,'pairs');for(const [label,value] of rows){if(value===undefined)continue;list.append(node('dt',label),node('dd',show(value)));}return list;}
function table(columns,rows){const t=node('table'),head=node('tr');for(const c of columns)head.append(node('th',c));const thead=node('thead');thead.append(head);const body=node('tbody');for(const r of rows){const tr=node('tr');for(const cell of r){const td=node('td');if(cell instanceof Node)td.append(cell);else td.textContent=show(cell);tr.append(td);}body.append(tr);}t.append(thead,body);const wrap=node('div',undefined,'table-wrap');wrap.append(t);return wrap;}
const profiles={general:'Общий профиль 112',fire:'Пожарная охрана',police:'Полиция',medical:'Скорая медицинская помощь',gas:'Аварийная газовая служба'},levels={basic:'Базовый',standard:'Стандартный',advanced:'Повышенный'};
const outcomes={timed_out:'Закрыта системой: не было действий',restarted:'Прервана для повтора'},closers={teacher:'преподаватель',system:'система',student:'обучающийся'};
function renderTask(r){
 const t=r.task;$('taskBody').replaceChildren(pairs([
  ['Занятие',t.lesson_title],['Задание / сценарий',t.title],['Режим',t.mode],['Своя служба',t.owner_service||undefined],
  ['Сложность',levels[t.difficulty]||t.difficulty],['Профиль ДДС',profiles[t.dds_profile]||t.dds_profile],
  ['Учебные цели',t.learning_objectives||undefined],['Канал',t.transport==='sip'?'IP-телефон (SIP)':'Текстовый диалог'],
  ['Подсказки',t.practice_with_hints?'Практика с подсказками':'Самостоятельно'],
  ['Обучающийся',r.student_name||undefined],['Поступила',date(r.times.created_at)],['Завершена',date(r.times.finished_at)],
  ['Время работы',span(r.times.elapsed_seconds)],['Кто завершил',closers[r.times.completed_by]||undefined],['Итог попытки',outcomes[r.times.attempt_outcome]||undefined]]));
}
function renderCard(r){
 const norms=$('norms');norms.replaceChildren();
 if(!r.norms.length)norms.append(node('p','Для этой попытки нормативы времени не заданы.','hint'));
 for(const n of r.norms){const box=node('article',undefined,'norm');box.append(node('h3',n.label),node('p',`Норматив ${span(n.limit_seconds)} · факт ${span(n.actual_seconds)}`),mark(n.within));norms.append(box);}
 const limits=r.norms.filter(n=>n.limit_seconds).map(n=>({at:n.limit_seconds,label:`Предел норматива «${n.label}»`}));
 const rows=[];let pending=[...limits].sort((a,b)=>a.at-b.at);
 for(const e of r.timeline){
  while(pending.length&&e.offset_seconds>pending[0].at){const l=pending.shift();rows.push([clock(l.at),node('span','Норматив','status neutral'),l.label,'']);}
  rows.push([clock(e.offset_seconds),node('span',e.actor==='student'?'Обучающийся':'Система',`actor ${e.actor}`),e.label,e.detail]);
 }
 $('timeline').replaceChildren(node('h3','Хронология от поступления карточки'),table(['Время','Кто','Событие','Подробности'],rows));
}
async function recording(call,format){const data=await api(`${base}/calls/${encodeURIComponent(call.call_id)}/recording?format=${format}`);return {data,blob:new Blob([Uint8Array.from(atob(data.file_base64),c=>c.charCodeAt(0))],{type:data.content_type})};}
function saveBlob(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function playRecording(button,call){
 button.disabled=true;button.textContent='Загрузка записи…';
 try{const {data,blob}=await recording(call,'mp3');
  const audio=node('audio');audio.controls=true;audio.src=URL.createObjectURL(blob);
  const tools=node('span',undefined,'record-tools'),mp3=node('button','Скачать MP3'),wav=node('button','Скачать WAV');mp3.type=wav.type='button';
  const name=`zvonok-${r_number}-${call.kind.replace(/[^a-zа-яё0-9]+/gi,'-')}`;
  mp3.onclick=()=>saveBlob(blob,name+'.mp3');
  wav.onclick=async()=>{wav.disabled=true;try{saveBlob((await recording(call,'wav')).blob,name+'.wav');}catch(error){wav.textContent='WAV недоступен';}finally{wav.disabled=false;}};
  tools.append(node('small',` ${data.duration_seconds} с${data.size_bytes?` · MP3 ${Math.max(1,Math.round(data.size_bytes/1024))} КБ`:''} `),mp3,wav);
  button.replaceWith(audio,tools);audio.play().catch(()=>{});}
 catch(error){button.textContent=/не найден/i.test(error.message)?'Запись недоступна':'Не удалось загрузить запись';}
}
let r_number='';
function renderCalls(r){
 const root=$('callsBody');root.replaceChildren();
 if(!r.calls.length){root.append(node('p','В этой попытке звонков и текстового диалога не было.','hint'));return;}
 for(const call of r.calls){
  const box=node('article',undefined,'call');const head=node('div',undefined,'call-head');head.append(node('h3',call.kind));
  if(call.call_id){const listen=node('button','Прослушать запись');listen.type='button';listen.onclick=()=>playRecording(listen,call);head.append(listen);}
  box.append(head);
  if(!call.transcript.length)box.append(node('p','Расшифровка не сохранена.','hint'));
  const list=node('ol',undefined,'transcript');for(const line of call.transcript){const item=node('li',undefined,line.role);item.append(node('b',line.role==='student'?'Обучающийся':'Собеседник'),node('span',line.text));list.append(item);}
  box.append(list);root.append(box);
 }
}
function renderDecision(r){
 const d=r.decision,root=$('decisionBody');root.replaceChildren();
 if(d.criteria.length)root.append(node('h3','Поля карточки и эталон'),table(['Поле','Эталон','Ответ обучающегося','Итог','Что исправить'],d.criteria.map(c=>[c.label,c.expected,c.actual,mark(c.passed),c.passed?'':c.recommendation])));
 if(d.dds_checks.length)root.append(node('h3','Решения диспетчера ДДС'),table(['Проверка','Итог','Пояснение'],d.dds_checks.map(c=>[c.label+(c.critical?' (критично)':''),mark(c.passed),c.detail])));
 if(d.services.length)root.append(node('h3','Службы и их статусы'),table(['Служба','Статус','Комментарий','Время'],d.services.map(s=>[s.service,s.status,s.comment,date(s.at)])));
 if(d.card.length)root.append(node('h3','Итоговая карточка'),pairs(d.card.map(x=>[x.label,x.value])));
 if(!root.children.length)root.append(node('p','Эталон для этой попытки не настроен.','hint'));
}
function renderVerdict(r){
 const root=$('verdictBody');root.replaceChildren();const a=r.assessment;
 if(!a){root.append(node('p','Оценка появится после завершения попытки.','hint'));return;}
 const e=a.effective,score=node('div',undefined,'score');
 score.append(node('strong',e.score_percent===null||e.score_percent===undefined?'Без оценки':`${e.score_percent}%`),e.passed===true?node('span','Зачтено','status good'):e.passed===false?node('span','Не зачтено','status critical'):node('span','Решение не определено','status neutral'),node('small',e.source==='expert'?'Оценка эксперта-преподавателя':'Автоматическая оценка'));
 root.append(score);
 if(a.expert)root.append(node('p',`Экспертное решение: ${a.expert.score_percent}% · ${a.expert.passed?'зачтено':'не зачтено'} · ${a.expert.teacher_name||''}. Обоснование: ${a.expert.reason}`));
 const rows=[];if(a.automatic_score!==null&&a.automatic_score!==undefined)rows.push(['Балл по эталону полей',`${a.automatic_score}%`]);if(a.dds_score!==null&&a.dds_score!==undefined)rows.push(['Балл решений ДДС',`${a.dds_score}%`]);
 if(a.policy_result)rows.push(['Правила преподавателя',a.policy_result.passed===true?'выполнены':a.policy_result.passed===false?'не выполнены':'не определено'],['Ошибок последовательности',a.policy_result.sequence_errors]);
 if(rows.length)root.append(pairs(rows));
 const g=r.grammar;if(g){root.append(node('h3','Ручной ввод и грамотность'),node('p',`Ошибок ввода: ${g.errors??0}, из них критичных: ${g.critical_errors??0}.`));const items=[...(g.typos||[]),...(g.mechanical||[])];if(items.length)root.append(table(['Поле','Что найдено'],items.slice(0,20).map(x=>[x.field,[x.fragment?'«'+x.fragment+'»':'',x.hint||''].filter(Boolean).join(' ')])));}
 const ai=r.ai_review;if(ai&&['ready','mock'].includes(ai.status)){root.append(node('h3','ИИ-разбор (рекомендательный)'));if(ai.summary)root.append(node('p',ai.summary));const items=(ai.findings||[]).filter(f=>f.kind!=='good');if(items.length)root.append(table(['Замечание','Цитата','Как лучше'],items.map(f=>[f.explanation,f.quote,f.suggestion])));}
}
const params=new URLSearchParams(location.search),sid=params.get('session');let base='';
(async()=>{try{
 if(!sid||!/^[0-9a-f-]{36}$/i.test(sid))throw Error('Не указана попытка');
 const me=await api('/api/v1/auth/me');base=`/api/v1/${me.role==='teacher'?'instructor':'student'}/sessions/${sid}`;
 if(me.role!=='teacher'&&me.role!=='student')throw Error('Разбор доступен преподавателю и обучающемуся');
 const r=await api(base+'/review');
 $('title').textContent=`Разбор попытки № ${r.number||'—'}`;r_number=r.number||'';$('subtitle').textContent=`${r.task.title||'Учебная попытка'}${r.student_name?' · '+r.student_name:''} · ${r.status}`;
 renderTask(r);renderCard(r);renderCalls(r);renderDecision(r);renderVerdict(r);
}catch(error){$('status').textContent=error.message;$('status').className='error';}})();
$('print').onclick=()=>window.print();
