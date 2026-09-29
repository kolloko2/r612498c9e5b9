'use strict';

const el=id=>document.getElementById(id);
for(const [formId,id] of [['assignmentForm','assignmentPractice'],['lessonForm','lessonPractice']]){
 const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.id=id;label.className='check';
 label.append(input,document.createTextNode(' Практика с подсказками'));if(formId==='lessonForm')el('lessonNorms').append(label);else el(formId).querySelector('button').before(label);
}
const roleNames={admin:'Администратор',teacher:'Преподаватель',student:'Студент'};
const detailNames={number:'Номер',status:'Статус',created_at:'Создано',finished_at:'Завершено',student_id:'Студент',assignment_id:'Задание',difficulty:'Сложность',dds_profile:'Профиль ДДС',learning_objectives:'Учебные цели',caller_name:'Заявитель',caller_status:'Статус заявителя',phone:'Телефон',supplied_phone:'Переданный телефон',scene_phone:'Телефон с места',country:'Страна',region:'Регион',city:'Город',district:'Район',area:'Территория',object:'Объект',street:'Улица',house:'Дом',building:'Корпус',structure:'Строение',apartment:'Квартира',entrance:'Подъезд',floor:'Этаж',code:'Код двери',address_note:'Уточнение адреса',description:'Описание',incident_type:'Тип происшествия',place:'Место',sign:'Признак',detail:'Деталь',injured:'Есть пострадавшие',no_access:'Нет доступа',threat_to_people:'Угроза людям',offense:'Правонарушение',injured_offsite:'Пострадавший вне места',gasification:'Газификация',refused:'Отказ',no_contact:'Нет контакта',interrupted:'Связь прервана',services:'Выбранные службы'};
let students=[],groups=[],assignments=[];
let scenarioPool=[],sessionSourcePool=[],lessonReport=null,teacherReady=false,routingServices=[];
let openSessionId=null,sessionRequestVersion=0,sessionRefreshInFlight=false;
let liveLessonId=null,liveLessonTitle='',liveRequestVersion=0,liveRefreshInFlight=false,teacherPollInFlight=false;
const lessonExtensions=new Map();
const fallbackCurriculum={difficulties:[{id:'basic',title:'Базовый'},{id:'standard',title:'Стандартный'},{id:'advanced',title:'Повышенный'}],profiles:[{id:'general',title:'Общий'},{id:'fire',title:'Пожарная охрана'},{id:'police',title:'Полиция'},{id:'medical',title:'Скорая медицинская помощь'},{id:'gas',title:'Аварийная газовая служба'},{id:'utilities',title:'ЖКХ и инженерные сети'}]};
let curriculum=fallbackCurriculum;
function curriculumTitle(kind,id){return curriculum[kind].find(x=>x.id===id)?.title||id||'Не указан';}
function addLessonCurriculumControls(){
 const categories=el('lessonCategories'),wrap=node('div',undefined,'columns');
 const difficulty=document.createElement('select'),profile=document.createElement('select');difficulty.id='lessonDifficulty';profile.id='lessonProfile';
 difficulty.add(new Option('Любая сложность',''));profile.add(new Option('Любой профиль ДДС',''));
 for(const item of curriculum.difficulties)difficulty.add(new Option(item.title,item.id));for(const item of curriculum.profiles)profile.add(new Option(item.title,item.id));
 const dl=node('label');dl.append('Сложность',difficulty);const pl=node('label');pl.append('Профиль ДДС',profile);wrap.append(dl,pl);categories.closest('label').before(wrap);
 difficulty.onchange=refreshLessonChoices;profile.onchange=refreshLessonChoices;
}
function addLessonTransportControls(){
 if(el('lessonTransport'))return;
 const modeLabel=el('lessonMode').closest('label'),wrap=node('div',undefined,'stack');wrap.id='lessonTransportControls';
 const label=node('label');label.append('Канал карточек');const select=document.createElement('select');select.id='lessonTransport';select.add(new Option('Текстовый диалог','text'));select.add(new Option('SIP-звонок','sip'));label.append(select);
 const note=node('p','Для SIP укажите внутренний номер каждого студента группы. Номера должны быть уникальны и заранее добавлены в Voice ALLOWED_EXTENSIONS и Asterisk.');note.id='lessonTransportNote';
 const extensions=node('div',undefined,'stack');extensions.id='lessonExtensions';wrap.append(label,note,extensions);(el('lessonHow')?.querySelector('legend')||modeLabel).after(wrap);
 select.onchange=refreshLessonTransportControls;el('lessonGroup').addEventListener('change',refreshLessonTransportControls);
 refreshLessonTransportControls();
}
function currentLessonGroup(){return groups.find(group=>group.id===el('lessonGroup').value);}
function refreshLessonTransportControls(){
 const select=el('lessonTransport'),container=el('lessonExtensions');if(!select||!container)return;
 // Телефония нужна и в режиме ДДС: по ней идёт доклад дежурному службы.
 const mode=el('lessonMode').value;select.disabled=false;
 el('lessonTransportNote').textContent=mode==='actions'?'Готовая карточка поступает данными; назначенный SIP-номер нужен для исходящего доклада дежурному. Номера должны быть доступны в Voice и Asterisk.':mode==='mixed'?'SIP используется для вызова заявителя в режиме 112 и для исходящего доклада в режиме ДДС. Номера должны быть доступны в Voice и Asterisk.':'Укажите внутренний номер каждого студента группы. Номера должны быть доступны в Voice и Asterisk.';
 container.hidden=select.value!=='sip';container.replaceChildren();if(container.hidden)return;
 const group=currentLessonGroup(),saved=lessonExtensions.get(group?.id)||{};lessonExtensions.set(group?.id,saved);
 for(const member of group?.students||[]){const label=node('label');label.append(member.display_name||member.username||member.id);const input=document.createElement('input');input.inputMode='numeric';input.pattern='[0-9]{1,8}';input.maxLength=8;input.required=true;input.placeholder='Например, 201';input.value=saved[member.id]||'';input.dataset.studentId=member.id;input.setAttribute('aria-label',`SIP-номер: ${member.display_name||member.id}`);input.addEventListener('input',()=>{saved[member.id]=input.value;});label.append(input);container.append(label);}
 if(!(group?.students||[]).length)container.append(node('p','В выбранной группе пока нет студентов.','empty'));
}
function lessonSipExtensions(){
 if(el('lessonTransport')?.value!=='sip')return {};
 const inputs=[...el('lessonExtensions').querySelectorAll('input[data-student-id]')],result={},used=new Set();
 if(!inputs.length)throw Error('Для SIP-занятия в группе должны быть студенты');
 for(const input of inputs){const value=input.value.trim();if(!/^[0-9]{1,8}$/.test(value))throw Error('Укажите для каждого студента SIP-номер из 1–8 цифр');if(used.has(value))throw Error('SIP-номера студентов должны быть уникальны');used.add(value);result[input.dataset.studentId]=value;}
 return result;
}
// Рабочие места и адресные задания задаются поштучно: обучающийся определяется
// номером места и получает конкретное задание на место.
const lessonPlaces=new Map(),lessonTargets=new Map();
function lessonScenarioOptions(){
 const mode=el('lessonMode').value,result=[];
 if(mode!=='actions')for(const option of el('lessonScenarios').selectedOptions)result.push({id:option.value,title:option.textContent});
 if(mode!=='fill')for(const option of el('lessonGenerated').selectedOptions)result.push({id:option.value,title:option.textContent});
 return result;
}
function refreshLessonPlaces(){
 const container=el('lessonPlaces');if(!container)return;
 const group=currentLessonGroup(),places=lessonPlaces.get(group?.id)||{},targets=lessonTargets.get(group?.id)||{};
 lessonPlaces.set(group?.id,places);lessonTargets.set(group?.id,targets);
 const scenarios=lessonScenarioOptions();
 container.replaceChildren(node('h3','Рабочие места и адресные задания'));
 if(!(group?.students||[]).length){container.append(node('p','В выбранной группе пока нет студентов.','empty'));return;}
 for(const member of group.students){
  const name=member.display_name||member.username||member.id,row=node('div',undefined,'columns');
  const place=node('label');place.append(`Место: ${name}`);
  const input=document.createElement('input');input.maxLength=80;input.placeholder='Например, АРМ-1';input.value=places[member.id]||'';
  input.dataset.placeStudentId=member.id;input.setAttribute('aria-label',`Рабочее место: ${name}`);
  input.addEventListener('input',()=>{places[member.id]=input.value;});place.append(input);
  const task=node('label');task.append(`Задание: ${name}`);
  const select=document.createElement('select');select.dataset.targetStudentId=member.id;
  select.setAttribute('aria-label',`Адресное задание: ${name}`);
  select.add(new Option('Любой сценарий занятия',''));
  for(const scenario of scenarios)select.add(new Option(scenario.title,scenario.id));
  select.value=scenarios.some(s=>s.id===targets[member.id])?targets[member.id]:'';
  targets[member.id]=select.value;
  select.onchange=()=>{targets[member.id]=select.value;};
  task.append(select);row.append(place,task);container.append(row);}
}
function lessonWorkstations(){
 const result={};
 for(const input of el('lessonPlaces').querySelectorAll('input[data-place-student-id]')){
  const value=input.value.trim();if(value)result[input.dataset.placeStudentId]=value;}
 return result;
}
function lessonStudentScenarios(){
 const result={};
 for(const select of el('lessonPlaces').querySelectorAll('select[data-target-student-id]')){
  if(select.value)result[select.dataset.targetStudentId]=select.value;}
 return result;
}
function refreshLessonChoices(){
 const categories=[...el('lessonCategories').selectedOptions].map(o=>o.value), mode=el('lessonMode').value,difficulty=el('lessonDifficulty')?.value||'',profile=el('lessonProfile')?.value||'';
 for(const id of ['lessonScenarios','lessonGenerated']){const select=el(id),selected=[...select.selectedOptions].map(o=>o.value);select.replaceChildren();for(const s of scenarioPool.filter(s=>s.enabled&&(!categories.length||categories.includes(s.category_id||'other'))&&(!difficulty||(s.difficulty||'basic')===difficulty)&&(!profile||(s.dds_profile||'general')===profile))){const option=new Option(`${s.title} · ${curriculumTitle('difficulties',s.difficulty||'basic')} · ${curriculumTitle('profiles',s.dds_profile||'general')}`,s.id);option.selected=selected.includes(s.id);select.add(option);}}
 const sources=el('lessonSources'),sourceSelection=[...sources.selectedOptions].map(o=>o.value);sources.replaceChildren();for(const s of sessionSourcePool.filter(s=>(!difficulty||(s.difficulty||'basic')===difficulty)&&(!profile||(s.dds_profile||'general')===profile))){const option=new Option(`№ ${s.number} · ${s.scenario_title||'Карточка'} · ${curriculumTitle('difficulties',s.difficulty||'basic')} · ${curriculumTitle('profiles',s.dds_profile||'general')}`,s.id);option.selected=sourceSelection.includes(s.id);sources.add(option);}
 el('lessonScenarios').disabled=mode==='actions';el('lessonScenarios').required=mode!=='actions'&&!categories.length&&!difficulty&&!profile;
 el('lessonSources').disabled=mode==='fill';el('lessonSources').required=false;el('lessonGenerated').disabled=mode==='fill';
 el('lessonCount').disabled=el('lessonUnlimited').checked;
 refreshLessonTransportControls();
 refreshLessonPlaces();
}
el('lessonCategories').onchange=refreshLessonChoices;el('lessonUnlimited').onchange=refreshLessonChoices;
for(const id of ['lessonScenarios','lessonGenerated'])el(id).addEventListener('change',refreshLessonPlaces);
el('lessonGroup').addEventListener('change',refreshLessonPlaces);
async function showLessonReport(id){
 lessonReport=await api(`/api/v1/instructor/lessons/${id}/report`);const r=lessonReport, container=el('lessonReportContent');container.replaceChildren(node('h3',r.title),node('p',`Участников: ${r.summary.participants}. Выдано: ${r.summary.issued}. Завершено: ${r.summary.completed}.`));
 if(r.reason)container.append(node('p','Причина завершения: '+r.reason));
 for(const person of r.participants){const section=node('section',undefined,'card');
  const place=person.workstation?` · ${person.workstation}`:'';
  section.append(node('h3',person.display_name+place),
   node('p',`Выполнено: ${person.completed} · орфографических ошибок в записях: ${person.grammar_errors??0} (грубых: ${person.critical_grammar_errors??0})`));
  for(const c of person.cards){
   section.append(node('p',`Попытка № ${c.attempt_number||1}${c.restarted_from?' · начата заново':''}${c.restarted_to?' · есть повторная попытка':''}${c.attempt_outcome==='restarted'?' · прервана для повтора':''}`));
   const t=c.timing||{},norm=value=>value===true?'в норме':value===false?'превышен':'не измерено';
   section.append(node('p',communicationText(c.communication)),node('p',`№ ${c.number} · ${c.title} · ${c.status} · ${c.score_percent==null?'без автоматической оценки':c.score_percent+'%'}`),
    node('p',c.exercise_mode==='actions'&&'first_record_seconds' in t
     ?`Открытие карточки: ${c.response_seconds??'—'} с из ${t.response_limit_seconds??'—'} (${norm(t.response_within_limit)}) · первая запись (статус и текст): ${t.first_record_seconds??'—'} с из ${t.limit_seconds??'—'} (${norm(t.within_limit)}) · всего в работе: ${c.elapsed_seconds??'—'} с (не нормируется)`
     :`${c.exercise_mode==='actions'?'Подтверждение получения':'Реакция'}: ${c.response_seconds??'—'} с из ${t.response_limit_seconds??'—'} (${norm(t.response_within_limit)}) · обработка: ${c.elapsed_seconds??'—'} с из ${t.limit_seconds??'—'} (${norm(t.within_limit)})`),
    button('Открыть карточку и историю',()=>act(async()=>{el('lessonReportDialog').close();await showSession(c.id);})));}
  container.append(section);}
 if(!el('lessonReportDialog').open)el('lessonReportDialog').showModal();
}
el('closeLessonReport').onclick=()=>el('lessonReportDialog').close();
el('exportLessonReport').onclick=()=>{if(!lessonReport)return;const url=URL.createObjectURL(new Blob([JSON.stringify(lessonReport,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=`lesson-${lessonReport.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
async function pollTeacher(){
 if(!teacherReady||document.hidden||teacherPollInFlight)return;teacherPollInFlight=true;
 try{await Promise.allSettled([loadLessons(),refreshOpenSession(),refreshLiveLesson()]);}finally{teacherPollInFlight=false;}
}
setInterval(pollTeacher,5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollTeacher();});

function setStatus(text,error=false){el('status').textContent=text;el('status').className=error?'error':'';}
async function api(path,method='GET',body){
 if(method==='POST'&&body&&path==='/api/v1/instructor/lessons')body={...body,practice_with_hints:el('lessonPractice').checked};
 if(method==='POST'&&body&&path==='/api/v1/instructor/assignments')body={...body,practice_with_hints:el('assignmentPractice').checked};
 const options={method,headers:{'X-Voice-UI':'1'}};
 if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options);
 if(response.status===401){location.replace('/login');throw Error('Требуется вход');}
 if(response.status===204)return null;
 const data=await response.json().catch(()=>({}));
 if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Не удалось выполнить запрос');
 return data;
}
async function act(operation,success){try{setStatus('');await operation();if(success)setStatus(success);}catch(error){setStatus(error.message,true);}}
function node(tag,text,className){const item=document.createElement(tag);if(text!==undefined)item.textContent=String(text);if(className)item.className=className;return item;}
function empty(container,text){container.replaceChildren(node('p',text,'empty'));}
function button(text,action){const item=node('button',text);item.type='button';item.addEventListener('click',action);return item;}
function field(label,value){const wrap=node('div');wrap.append(node('dt',label),node('dd',value===undefined||value===null||value===''?'—':value));return wrap;}

let me=null;
async function loadPolicy(){const policy=await api('/api/v1/admin/policy');el('policySession').value=policy.session_hours;el('policyFailures').value=policy.failure_limit;el('policyLock').value=policy.lock_seconds;el('policyRetention').value=policy.audit_retention_days;el('policyLogLevel').value=policy.log_level;for(const box of document.querySelectorAll('[name=mfaRole]'))box.checked=(policy.mfa_required_roles||[]).includes(box.value);}
el('policyForm').addEventListener('submit',event=>{event.preventDefault();act(async()=>{await api('/api/v1/admin/policy','PUT',{session_hours:+el('policySession').value,failure_limit:+el('policyFailures').value,lock_seconds:+el('policyLock').value,audit_retention_days:+el('policyRetention').value,log_level:el('policyLogLevel').value,mfa_required_roles:[...document.querySelectorAll('[name=mfaRole]:checked')].map(box=>box.value)});await loadPolicy();},'Политика сохранена');});
async function loadAdmin(){
 await loadPolicy();
 const users=await api('/api/v1/admin/users');const container=el('users');container.replaceChildren();
 if(!users.length){empty(container,'Пользователей пока нет');return;}
 for(const user of users){
  const card=node('article',undefined,'card'),head=node('div',undefined,'card-head'),title=node('div');
  title.append(node('h3',user.display_name),node('p',`${user.username} · ${roleNames[user.role]||user.role}`));
  head.append(title,node('span',user.active?'Активен':'Доступ закрыт',`badge${user.active?'':' off'}`));card.append(head);
  card.append(node('p',user.mfa_enabled?'Двухфакторный вход включён':'Вход только по паролю'));
  if(user.mfa_enabled&&user.id!==me?.id)card.append(button('Сбросить двухфакторный вход',()=>{if(!confirm(`Сбросить второй фактор пользователю ${user.display_name}? Используйте, если телефон утерян: при следующем входе приложение придётся подключить заново.`))return;act(async()=>{await api('/api/v1/admin/users/'+encodeURIComponent(user.id)+'/mfa-reset','POST');await loadAdmin();},'Двухфакторный вход сброшен');}));
  {const actions=node('div',undefined,'card-actions'),role=document.createElement('select');role.setAttribute('aria-label','Роль пользователя '+user.username);
   for(const [value,label] of Object.entries(roleNames))role.add(new Option(label,value,false,value===user.role));
   role.disabled=user.id===me?.id;role.onchange=()=>act(async()=>{try{await api('/api/v1/admin/users/'+encodeURIComponent(user.id)+'/role','PATCH',{role:role.value});}finally{await loadAdmin();}},'Роль изменена: права действуют со следующего входа');
   actions.append(role);if(user.id!==me?.id)actions.append(button(user.active?'Закрыть доступ':'Восстановить доступ',()=>act(async()=>{await api('/api/v1/admin/users/'+encodeURIComponent(user.id),'PATCH',{active:!user.active});await loadAdmin();},'Состояние пользователя изменено')));card.append(actions);}
  if(user.role!=='admin'){const directoryForm=node('form',undefined,'stack'),directoryName=document.createElement('input');directoryName.placeholder='Логин в LDAP / AD';directoryName.setAttribute('aria-label','Логин в LDAP / AD');directoryName.required=true;directoryName.pattern='[a-zA-Z0-9][a-zA-Z0-9_.\\-]{2,39}';directoryName.maxLength=40;const submit=node('button','Связать с каталогом');directoryForm.append(directoryName,submit);directoryForm.onsubmit=event=>{event.preventDefault();if(!confirm('Разрешить владельцу этой учётной записи LDAP входить в выбранный профиль?'))return;act(async()=>{await api('/api/v1/admin/users/'+encodeURIComponent(user.id)+'/directory','POST',{username:directoryName.value});directoryName.value='';},'Связь с каталогом сохранена');};card.append(directoryForm);}
  container.append(card);
 }
}

async function loadGroups(){
 [students,groups]=await Promise.all([api('/api/v1/instructor/students'),api('/api/v1/instructor/groups')]);
// Архивные группы не предлагаются для новых заданий и занятий.
 const working=groups.filter(group=>!group.archived);
const select=el('assignmentGroup');select.replaceChildren();for(const group of working)select.add(new Option(group.title,group.id));el('lessonGroup').replaceChildren();for(const group of working)el('lessonGroup').add(new Option(group.title,group.id));
 refreshLessonTransportControls();
 const container=el('groups');container.replaceChildren();if(!groups.length){empty(container,'Создайте первую группу');return;}
 const archive=node('details',undefined,'lessons-done');archive.append(node('summary',`Архив групп (${groups.length-working.length})`));
 if(!working.length)empty(container,'Все группы в архиве');
 for(const group of groups){
  const card=node('article',undefined,'card');card.append(node('h3',group.title));
  if(!group.students.length)card.append(node('p','В группе пока нет студентов'));
  else{const list=node('ul',undefined,'group-members');for(const item of group.students){const row=node('li');row.append(node('span',item.display_name));if(!group.archived)row.append(button('Убрать',()=>{if(!confirm(`Убрать ${item.display_name} из группы «${group.title}»? Новые карточки этой группы ученик получать не будет, прежние результаты сохранятся.`))return;act(async()=>{await api(`/api/v1/instructor/groups/${encodeURIComponent(group.id)}/members/${encodeURIComponent(item.id)}`,'DELETE');await loadGroups();},'Ученик убран из группы');}));list.append(row);}card.append(list);}
  card.append(button(group.archived?'Вернуть из архива':'В архив',()=>act(async()=>{await api(`/api/v1/instructor/groups/${encodeURIComponent(group.id)}`,'PATCH',{archived:!group.archived});await loadGroups();},group.archived?'Группа возвращена из архива':'Группа перенесена в архив')));
  if(group.archived){archive.append(card);continue;}
  const available=students.filter(item=>!group.member_ids.includes(item.id));
  if(available.length){const form=node('form',undefined,'inline'),selectStudent=document.createElement('select');selectStudent.setAttribute('aria-label','Студент');for(const item of available)selectStudent.add(new Option(`${item.display_name} (${item.username})`,item.id));const add=node('button','Добавить');add.className='primary';form.append(selectStudent,add);form.addEventListener('submit',event=>{event.preventDefault();act(async()=>{await api(`/api/v1/instructor/groups/${encodeURIComponent(group.id)}/members`,'POST',{student_id:selectStudent.value});await loadGroups();},'Студент добавлен в группу');});card.append(form);}
  container.append(card);
 }
 if(groups.length>working.length)container.append(archive);
}

async function loadAssignments(){
 assignments=await api('/api/v1/instructor/assignments');const container=el('assignments');container.replaceChildren();
 if(!assignments.length){empty(container,'Заданий пока нет');return;}
 for(const item of assignments){const card=node('article',undefined,'card'),group=groups.find(value=>value.id===item.group_id),scenario=scenarioPool.find(value=>value.id===item.scenario_id),source=scenario||item;card.append(node('h3',item.title),node('p',`${group?.title||'Группа'} · сценарий ${item.scenario_id}`),node('p',`${curriculumTitle('difficulties',source.difficulty||'basic')} · ${curriculumTitle('profiles',source.dds_profile||'general')}`));if(source.learning_objectives)card.append(node('p','Учебные цели: '+source.learning_objectives));const actions=node('div',undefined,'card-actions');actions.append(node('span',item.active?'Активно':'Приостановлено',`badge${item.active?'':' off'}`),button(item.active?'Приостановить':'Включить',()=>act(async()=>{await api('/api/v1/instructor/assignments/'+encodeURIComponent(item.id),'PATCH',{active:!item.active});await loadAssignments();},'Задание изменено')));card.append(actions);container.append(card);}
}

function addAssignmentPracticeControls(){
 [...el('assignments').querySelectorAll('article')].forEach((card,index)=>{
  const item=assignments[index];if(!item||card.querySelector('[data-practice]'))return;
  if(!item.practice_with_hints&&item.practice_available===false){const note=node('p','Подсказки недоступны: сначала утвердите их в редакторе сценария','muted');note.dataset.practice='true';card.append(note);return;}
  const toggle=button(item.practice_with_hints?'Подсказки разрешены — выключить':'Разрешить практику с подсказками',()=>act(async()=>{
   await api('/api/v1/instructor/assignments/'+encodeURIComponent(item.id),'PATCH',{active:item.active,practice_with_hints:!item.practice_with_hints});await loadAssignments();
  },'Настройка применяется к новым попыткам'));toggle.dataset.practice='true';card.append(toggle);
 });
}
new MutationObserver(addAssignmentPracticeControls).observe(el('assignments'),{childList:true});
async function loadSessions(){
 const sessions=await api('/api/v1/instructor/sessions');sessionSourcePool=sessions.filter(s=>s.status==='Завершена');refreshLessonChoices();const container=el('sessions');container.replaceChildren();
 if(!sessions.length){empty(container,'У назначенных вам занятий пока нет попыток');return;}
 // Последние попытки на виду, остальные свёрнуты, чтобы кабинет не превращался в длинную ленту.
 const older=node('details',undefined,'lessons-done');older.append(node('summary',`Более ранние попытки (${Math.max(0,sessions.length-10)})`));
 for(const [index,item] of sessions.entries()){const card=node('article',undefined,'card'),student=students.find(value=>value.id===item.student_id),head=node('div',undefined,'card-head'),title=node('div');title.append(node('h3',`№ ${item.number||'—'} · ${item.scenario_title||'Сценарий'}`),node('p',`${student?.display_name||'Студент'} · ${item.status||'—'} · ${new Date(item.created_at).toLocaleString('ru-RU')}`));head.append(title,node('span',item.score_percent===null||item.score_percent===undefined?'Без оценки':`${item.score_percent}%`,'badge'));card.append(head,node('p',communicationText(item.communication)),node('p',`Попытка № ${item.attempt_number||1}${item.restarted_from?' · начата заново':''}${item.restarted_to?' · есть повторная попытка':''}${item.attempt_outcome==='restarted'?' · прервана для повтора':''}${item.attempt_outcome==='timed_out'?' · закрыта системой: нет действий':''}`));const route=node('a','Сквозной разбор','button');route.href='/review?session='+encodeURIComponent(item.id);card.append(button('Открыть',()=>act(()=>showSession(item.id))),route);(index<10?container:older).append(card);}
 if(sessions.length>10)container.append(older);
}

async function loadLessons(){
 const values=await api('/api/v1/instructor/lessons');el('lessons').replaceChildren();
 // Идущие занятия сверху; завершённые свёрнуты, чтобы не теряться в длинном списке.
 const finishedCount=values.filter(v=>v.state==='finished').length,done=node('details',undefined,'lessons-done');done.append(node('summary',`Завершённые занятия (${finishedCount})`));
 const labels={planned:'Подготовлено',running:'Идёт',stopping:'Завершается — повторите завершение',finished:'Завершено'};
 for(const value of values){const card=node('article',undefined,'card'),meta=[value.difficulty&&curriculumTitle('difficulties',value.difficulty),value.dds_profile&&curriculumTitle('profiles',value.dds_profile)].filter(Boolean).join(' · ');card.append(node('h3',value.title),node('p',`${labels[value.state]} · ${value.cards_per_student===null?'До остановки преподавателем':value.cards_per_student+' карточек каждому'}${meta?' · '+meta:''} · завершено ${value.cards.filter(c=>c.status==='Завершена').length} из ${value.cards.length} выданных`));
 if(['planned','running'].includes(value.state))card.append(button('Настроить IP-телефоны',()=>configurePhones(value)));
 if(value.state==='planned')card.append(button('Начать',()=>act(async()=>{await api(`/api/v1/instructor/lessons/${value.id}/start`,'POST');await loadLessons();})));
 card.append(node('p',value.practice_with_hints?'Практика с подсказками':'Самостоятельное выполнение без подсказок'));
 if(['planned','running'].includes(value.state)&&!value.practice_with_hints&&value.practice_available===false)card.append(node('p','Подсказки недоступны: не у всех сценариев занятия утверждены подсказки','muted'));
 else if(['planned','running'].includes(value.state))card.append(button(value.practice_with_hints?'Выключить подсказки':'Разрешить подсказки',()=>act(async()=>{await api(`/api/v1/instructor/lessons/${value.id}/practice`,'PUT',{practice_with_hints:!value.practice_with_hints});await loadLessons();},'Настройка подсказок сохранена')));
 if(value.state==='running'&&value.practice_with_hints){
  // «Делай как я»: преподаватель ведёт группу по шагам вводного курса.
  const guided=node('div',undefined,'guided-control');
  const step=document.createElement('input');step.type='number';step.min='1';step.max='11';
  step.value=value.guided_step||1;step.setAttribute('aria-label','Шаг обучения');
  guided.append(node('span','Показ шага группе:'),step,
   button('Показать',()=>act(async()=>{
    await api(`/api/v1/instructor/lessons/${value.id}/guided-step`,'PUT',{step:Number(step.value)});
    await loadLessons();})),
   button('Убрать показ',()=>act(async()=>{
    await api(`/api/v1/instructor/lessons/${value.id}/guided-step`,'PUT',{step:null});
    await loadLessons();})));
  if(value.guided_step)guided.append(node('span',`сейчас показывается шаг ${value.guided_step}`,'muted'));
  card.append(guided);
 }
 if(['planned','running','stopping'].includes(value.state))card.append(button('Завершить всем',()=>openLessonStop(value)));
 // Пройти то же занятие с нуля: прежние результаты остаются в отчётах.
 card.append(button('Начать заново',()=>{if(!confirm(`Начать «${value.title}» заново? Текущие карточки будут завершены, группа получит новые с нуля. Прежние результаты сохранятся в отчётах.`))return;act(async()=>{await api(`/api/v1/instructor/lessons/${value.id}/restart`,'POST');await loadLessons();await loadSessions();},'Занятие начато заново');}));
 card.append(button('Участники в реальном времени',()=>openLiveLesson(value.id,value.title)),button('Отчёт и участники',()=>act(()=>showLessonReport(value.id))));(value.state==='finished'?done:el('lessons')).append(card);}
 if(finishedCount)el('lessons').append(done);
}
el('lessonMode').onchange=()=>{refreshLessonChoices();const mixed=el('lessonMode').value==='mixed';el('lessonModeSwitch').disabled=!mixed;if(!mixed)el('lessonModeSwitch').checked=false;};el('lessonModeSwitch').disabled=el('lessonMode').value!=='mixed';
el('lessonForm').onsubmit=event=>{event.preventDefault();const submit=event.submitter;submit.disabled=true;act(async()=>{const transport=el('lessonTransport').value,sip_extensions=transport==='sip'?lessonSipExtensions():{};await api('/api/v1/instructor/lessons','POST',{title:el('lessonTitle').value,group_id:el('lessonGroup').value,mode:el('lessonMode').value,allow_mode_switch:el('lessonModeSwitch').checked,transport,sip_extensions,difficulty:el('lessonDifficulty').value||null,dds_profile:el('lessonProfile').value||null,category_ids:[...el('lessonCategories').selectedOptions].map(o=>o.value),prefilled_scenario_ids:el('lessonMode').value==='fill'?[]:[...el('lessonGenerated').selectedOptions].map(o=>o.value),scenario_ids:el('lessonMode').value==='actions'?[]:[...el('lessonScenarios').selectedOptions].map(o=>o.value),source_session_ids:el('lessonMode').value==='fill'?[]:[...el('lessonSources').selectedOptions].map(o=>o.value),cards_per_student:el('lessonUnlimited').checked?null:Number(el('lessonCount').value),parallel_cards:Number(el('lessonParallel').value)||1,adaptive_difficulty:el('lessonAdaptive').checked,workstations:lessonWorkstations(),student_scenarios:lessonStudentScenarios(),norm_seconds:el('lessonNorm').value?Number(el('lessonNorm').value):null,pass_score_percent:el('lessonPass').value===''?null:Number(el('lessonPass').value)});await loadLessons();},'Занятие подготовлено. Нажмите «Начать».').finally(()=>submit.disabled=false);};

function ensureLiveDialog(){
 let dialog=el('liveLessonDialog');if(dialog)return dialog;dialog=document.createElement('dialog');dialog.id='liveLessonDialog';const head=node('div',undefined,'dialog-head'),title=node('h2');title.id='liveLessonHeading';const close=button('Закрыть',()=>dialog.close());head.append(title,close);const observed=node('p');observed.id='liveLessonObserved';const content=node('div');content.id='liveLessonContent';dialog.append(head,observed,content);document.body.append(dialog);dialog.addEventListener('close',()=>{liveLessonId=null;liveRequestVersion++;});return dialog;
}
function formatElapsed(seconds){const total=Math.max(0,Number(seconds)||0),minutes=Math.floor(total/60),rest=Math.floor(total%60);return minutes?`${minutes} мин ${String(rest).padStart(2,'0')} сек`:`${rest} сек`;}
function persistedEventText(event){
 if(!event)return 'Сохранённых действий пока нет';if(typeof event==='string')return event;
 const type=event.type||event.event_type||'действие',at=event.at||event.created_at||event.timestamp,detail=event.detail||event.status||event.service;
 return [type,detail&&(typeof detail==='string'?detail:JSON.stringify(detail)),at&&new Date(at).toLocaleString('ru-RU')].filter(Boolean).join(' · ');
}
function renderLiveLesson(value){
 el('liveLessonHeading').textContent=`Участники · ${value.title||liveLessonTitle}`;el('liveLessonObserved').textContent=`Состояние: ${{planned:'подготовлено',running:'идёт',stopping:'завершается',finished:'завершено'}[value.state]||value.state} · данные на ${new Date(value.observed_at).toLocaleString('ru-RU')}`;
 const container=el('liveLessonContent');container.replaceChildren();if(!value.participants?.length){empty(container,'Участников пока нет');return;}
 for(const person of value.participants){const card=node('article',undefined,'card');card.append(node('h3',person.display_name||person.student_id),node('p',`Завершено карточек: ${person.completed??0}`));
  if(person.active_card){const active=person.active_card;card.append(node('p',`Активная карточка № ${active.number??'—'} · ${active.title||'Без названия'}`),node('p',`Прошло: ${formatElapsed(active.elapsed_seconds)} · канал: ${active.transport||'не указан'}`),node('p','Последнее сохранённое действие: '+persistedEventText(active.last_event)));if(active.call_id)card.append(node('p','Идентификатор звонка: '+active.call_id));if(active.provider_error)card.append(node('p','Ошибка провайдера: '+(typeof active.provider_error==='string'?active.provider_error:JSON.stringify(active.provider_error)),'error'));card.append(button('Открыть карточку',()=>{ensureLiveDialog().close();act(()=>showSession(active.id));}));
  }else card.append(node('p','Активной карточки нет'));
  if(person.latest_result){const score=person.latest_result.score_percent==null?'без автоматической оценки':`${person.latest_result.score_percent}%`,policy=person.latest_result.policy_result?.passed;card.append(node('p',`Последний зафиксированный результат: ${score}${policy===true?' · политика выполнена':policy===false?' · политика не выполнена':''}`));}
  container.append(card);
 }
}
function openLessonStop(lesson){
 el('stopLessonDialog')?.remove();
 const dialog=node('dialog'),heading=node('h2','Завершить занятие группы'),form=node('form',undefined,'stack'),label=node('label','Причина завершения'),reason=document.createElement('textarea'),error=node('p'),actions=node('div',undefined,'card-actions'),submit=node('button','Завершить занятие'),cancel=button('Отмена',()=>dialog.close());
 dialog.id='stopLessonDialog';heading.id='stopLessonHeading';dialog.setAttribute('aria-labelledby',heading.id);reason.required=true;reason.maxLength=1000;reason.rows=3;submit.type='submit';error.setAttribute('role','alert');label.append(reason);actions.append(submit,cancel);form.append(node('p',lesson.title),node('p','Новые карточки больше не поступят. Незавершённые работы будут остановлены и оценены по сохранённым действиям.'),label,error,actions);dialog.append(heading,form);document.body.append(dialog);
 form.onsubmit=async event=>{event.preventDefault();if(!reason.value.trim()){reason.setCustomValidity('Укажите причину завершения');reason.reportValidity();return;}submit.disabled=cancel.disabled=true;reason.disabled=true;try{await api(`/api/v1/instructor/lessons/${lesson.id}/finish`,'POST',{reason:reason.value.trim()});dialog.close();await loadLessons();await loadSessions();}catch(e){error.textContent=e.message;}finally{submit.disabled=cancel.disabled=false;reason.disabled=false;}};reason.oninput=()=>reason.setCustomValidity('');dialog.showModal();
}
async function openLiveLesson(id,title){
 const dialog=ensureLiveDialog();liveLessonId=id;liveLessonTitle=title;liveRequestVersion++;el('liveLessonHeading').textContent=`Участники · ${title}`;empty(el('liveLessonContent'),'Загрузка…');el('liveLessonObserved').textContent='';if(!dialog.open)dialog.showModal();await refreshLiveLesson();
}
async function refreshLiveLesson(){
 const dialog=el('liveLessonDialog');if(!liveLessonId||!dialog?.open||document.hidden||liveRefreshInFlight)return;liveRefreshInFlight=true;const id=liveLessonId,version=++liveRequestVersion;
 try{const value=await api(`/api/v1/instructor/lessons/${encodeURIComponent(id)}/live`);if(dialog.open&&liveLessonId===id&&version===liveRequestVersion)renderLiveLesson(value);}catch(error){if(dialog.open&&liveLessonId===id&&version===liveRequestVersion)el('liveLessonObserved').textContent='Не удалось обновить: '+error.message;}finally{liveRefreshInFlight=false;}
}

function renderObject(title,value){const section=node('section',undefined,'detail-section');section.append(node('h3',title));const list=node('dl',undefined,'detail-grid');for(const [key,item] of Object.entries(value||{})){if(item&&typeof item==='object'&&!Array.isArray(item))continue;const shown=Array.isArray(item)?item.join(', '):(typeof item==='boolean'?(item?'Да':'Нет'):item);list.append(field(detailNames[key]||key,shown));}section.append(list);return section;}
function sessionHost(name){const item=node('div');item.dataset.sessionHost=name;return item;}
function buildSessionShell(id){const detail=el('sessionDetail');detail.replaceChildren();detail.dataset.sessionId=id;for(const name of ['refresh-state','main','stop','vis','dialogue','evaluation','feedback-notes','feedback-form'])detail.append(sessionHost(name));}
function createStopForm(id){
 const form=node('form',undefined,'stack'),reason=document.createElement('textarea'),stop=node('button','Завершить занятие'),error=node('p');form.dataset.sessionStopForm=id;
 reason.required=true;reason.maxLength=1000;reason.placeholder='Причина завершения';reason.setAttribute('aria-label','Причина завершения');form.append(node('p','Будет оценена последняя сохранённая карточка. Несохранённые правки студента не входят в результат.'),reason,stop,error);
 form.onsubmit=async event=>{event.preventDefault();if(!reason.value.trim()||!confirm('Завершить занятие и зафиксировать результат?'))return;stop.disabled=true;try{await api(`/api/v1/instructor/sessions/${encodeURIComponent(id)}/finish`,'POST',{reason:reason.value.trim()});await showSession(id,{open:false});await loadSessions();}catch(e){error.textContent=e.message;stop.disabled=false;}};return form;
}
function createFeedbackForm(id){
 const form=node('form',undefined,'stack'),text=document.createElement('textarea'),send=node('button','Отправить студенту'),message=node('p');form.dataset.sessionFeedbackForm=id;text.required=true;text.maxLength=2000;text.setAttribute('aria-label','Замечание студенту');text.placeholder='Подсказка или замечание студенту';form.append(text,send,message);let pendingId=null;text.addEventListener('input',()=>{pendingId=null;});
 form.onsubmit=async event=>{event.preventDefault();if(!text.value.trim())return;send.disabled=true;text.disabled=true;pendingId=pendingId||crypto.randomUUID();try{await api(`/api/v1/instructor/sessions/${encodeURIComponent(id)}/feedback`,'POST',{message_id:pendingId,text:text.value.trim()});text.value='';pendingId=null;text.disabled=false;send.disabled=false;await showSession(id,{open:false});}catch(error){message.textContent=error.message;send.disabled=false;text.disabled=false;}};return form;
}
// Внешняя информационная система в тренажёре — это преподаватель: реальной
// интеграции нет. Добавленная так служба приходит в карточку с пометкой ВИС,
// как описано в инструкции оператора Системы 112.
function createVisForm(id, services){
 const form=node('form',undefined,'stack'),select=document.createElement('select'),
       reason=document.createElement('textarea'),send=node('button','Добавить от внешней системы'),message=node('p');
 form.dataset.sessionVisForm=id;
 select.setAttribute('aria-label','Служба от внешней системы');
 for(const name of services)select.add(new Option(name,name));
 reason.required=true;reason.maxLength=1000;reason.placeholder='Основание: почему служба добавлена внешней системой';
 reason.setAttribute('aria-label','Основание добавления');
 form.append(node('p','Служба появится в карточке обучающегося с пометкой ВИС. Данные не покидают стенд.'),select,reason,send,message);
 let pendingId=null;reason.addEventListener('input',()=>{pendingId=null;});
 form.onsubmit=async event=>{
  event.preventDefault();if(!select.value||!reason.value.trim())return;
  send.disabled=true;pendingId=pendingId||crypto.randomUUID();
  try{
   await api(`/api/v1/instructor/sessions/${encodeURIComponent(id)}/vis-service`,'POST',
             {message_id:pendingId,service:select.value,reason:reason.value.trim()});
   reason.value='';pendingId=null;message.textContent='Служба добавлена с пометкой ВИС';
   await showSession(id,{open:false});
  }catch(error){message.textContent=error.message;}
  finally{send.disabled=false;}
 };
 return form;
}
function configurePhones(lesson){
 const dialog=document.createElement('dialog'),form=document.createElement('form'),inputs={};
 form.append(node('h2','IP-телефоны участников'),node('p','Укажите номера, зарегистрированные на учебных телефонах. Настройка применяется к новым и ещё не начатым телефонным разговорам.'));
 const members=lesson.members?.length?lesson.members:(groups.find(g=>g.id===lesson.group_id)?.member_ids||[]);
 for(const uid of members){const label=document.createElement('label'),input=document.createElement('input');input.required=true;input.pattern='[0-9]{1,8}';input.value=lesson.sip_extensions?.[uid]||'';label.append(node('span',students.find(s=>s.id===uid)?.display_name||uid),input);form.append(label);inputs[uid]=input;}
 const save=document.createElement('button');save.type='submit';save.textContent='Сохранить и включить телефон';const cancel=button('Закрыть',()=>dialog.close()),message=node('p');form.append(save,cancel,message);
 form.onsubmit=async event=>{event.preventDefault();save.disabled=true;try{await api(`/api/v1/instructor/lessons/${lesson.id}/phones`,'PUT',{sip_extensions:Object.fromEntries(Object.entries(inputs).map(([id,input])=>[id,input.value.trim()]))});dialog.close();await loadLessons();}catch(error){message.textContent=error.message;}finally{save.disabled=false;}};
 dialog.append(form);document.body.append(dialog);dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
}
function communicationText(c){return c?`Общение: ${{text:'текст',sip:'голос по IP-телефону',mixed:'голос и текст',none:'реплик ученика ещё нет'}[c.mode]||'неизвестно'} · голосовых реплик: ${c.sip_turns} · текстовых: ${c.text_turns}`:'Способ общения не зафиксирован';}
function renderSession(value){
 const detail=el('sessionDetail'),id=value.id;if(detail.dataset.sessionId!==id)buildSessionShell(id);const host=name=>detail.querySelector(`[data-session-host="${name}"]`),refreshState=host('refresh-state'),main=host('main');refreshState.textContent='Обновлено: '+new Date().toLocaleTimeString('ru-RU');refreshState.className='';main.replaceChildren();main.append(node('p',communicationText(value.communication)));
 main.append(renderObject('Сведения',{number:value.number,status:value.status,created_at:value.created_at,finished_at:value.finished_at,student_id:value.student_id,assignment_id:value.assignment_id,difficulty:curriculumTitle('difficulties',value.difficulty||'basic'),dds_profile:curriculumTitle('profiles',value.dds_profile||'general'),learning_objectives:value.learning_objectives}),renderObject('Карточка',value.card),node('p','Статус карточки АРМ: '+(value.incident_status||'не указан')));
 for(const item of value.notifications||[])main.append(renderObject('Телефонограмма',item));for(const item of value.linked_cards||[])main.append(button('Связанная карточка № '+item.number,()=>act(()=>showSession(item.id))));if(value.action_report)main.append(renderObject('Действия с готовой карточкой',value.action_report));if(value.dds_review){const r=value.dds_review;main.append(node('h3','Решения диспетчера'),node('p',`${r.service} · выполнено ${r.passed_count} из ${r.total_count}`+(r.score_percent===null?'':` · ${r.score_percent}%`)));if(r.critical_errors.length)main.append(node('p','Критические ошибки: '+r.critical_errors.join('; '),'error'));for(const c of r.checks)main.append(node('p',`${c.passed===true?'✓':c.passed===false?'×':'○'} ${c.label}`+(c.detail?' — '+c.detail:'')));}for(const event of value.events||[])if(event.type==='service.updated')main.append(renderObject('Реагирование службы',event.detail));if(value.completed_by)main.append(renderObject('Завершение занятия',value.completed_by));
 main.append(node('p',`Попытка № ${value.attempt_number||1}${value.attempt_outcome==='restarted'?' · прервана для повторного прохождения':''}`));
 if(value.restarted_from)main.append(button('Предыдущая попытка',()=>act(()=>showSession(value.restarted_from))));
 if(value.restarted_to)main.append(button('Новая попытка',()=>act(()=>showSession(value.restarted_to))));
 else main.append(button('Начать эту карточку ученика заново',()=>{if(!confirm('Начать карточку с нуля? Предыдущая попытка останется в истории.'))return;act(async()=>{const next=await api(`/api/v1/instructor/sessions/${id}/restart`,'POST',{});await showSession(next.id);await loadSessions();},'Создана новая попытка');}));
 const stopHost=host('stop');if(value.status==='Завершена')stopHost.replaceChildren();else if(!stopHost.querySelector(`[data-session-stop-form="${id}"]`))stopHost.replaceChildren(createStopForm(id));
 const visHost=host('vis');
 const visAvailable=(routingServices||[]).filter(name=>!(value.card?.services||[]).includes(name));
 if(value.status==='Завершена'||!value.revision||!visAvailable.length)visHost.replaceChildren();
 else if(!visHost.querySelector(`[data-session-vis-form="${id}"]`)){
  visHost.className='detail-section';
  visHost.replaceChildren(node('h3','Служба от внешней системы (ВИС)'),createVisForm(id,visAvailable));
 }
 const dialogue=host('dialogue');dialogue.replaceChildren(node('h3','Диалог'));dialogue.className='detail-section';if(value.messages?.length){for(const message of value.messages){const line=node('div',undefined,`message ${message.role==='assistant'?'assistant':''}`);line.append(node('strong',message.role==='assistant'?'Заявитель':'Студент'),node('p',message.content));dialogue.append(line);}}else dialogue.append(node('p','Реплик нет'));
 const evaluation=host('evaluation');evaluation.replaceChildren();if(value.evaluation||value.dds_review){evaluation.className='detail-section';const score=value.exercise_mode==='actions'&&value.dds_review?value.dds_review.score_percent:value.evaluation?.score_percent;evaluation.append(node('h3','Оценка'),node('p',score===null||score===undefined?'Оценка не настроена':`${score}%`,'score'));for(const criterion of value.evaluation?.criteria||[])evaluation.append(node('p',`${criterion.passed?'✓':'×'} ${criterion.label}: ${criterion.recommendation||''}`));}
 const notes=host('feedback-notes');notes.replaceChildren(node('h3','Обратная связь преподавателя'));notes.className='detail-section';for(const note of value.teacher_feedback||[])notes.append(node('p',`${new Date(note.at).toLocaleString('ru-RU')} · ${note.teacher_name}: ${note.text}`));
 const formHost=host('feedback-form');if(!formHost.querySelector(`[data-session-feedback-form="${id}"]`))formHost.replaceChildren(createFeedbackForm(id),button('Обновить карточку и диалог',()=>act(()=>showSession(id,{open:false}))));
}
async function showSession(id,options={}){
 openSessionId=id;const version=++sessionRequestVersion,value=await api('/api/v1/instructor/sessions/'+encodeURIComponent(id));if(openSessionId!==id||version!==sessionRequestVersion)return;renderSession(value);if(options.open!==false&&!el('sessionDialog').open)el('sessionDialog').showModal();
}
async function refreshOpenSession(){
 if(!openSessionId||!el('sessionDialog').open||sessionRefreshInFlight)return;sessionRefreshInFlight=true;const id=openSessionId;try{await showSession(id,{open:false});}catch(error){if(openSessionId===id&&el('sessionDialog').open){const state=el('sessionDetail').querySelector('[data-session-host="refresh-state"]');if(state){state.textContent='Данные могли устареть: '+error.message;state.className='error';}}}finally{sessionRefreshInFlight=false;}
}

async function loadTeacher(){await loadGroups();const scenarios=await api('/api/scenarios');el('scenarioId').replaceChildren();for(const scenario of scenarios.filter(s=>s.enabled))el('scenarioId').add(new Option(`${scenario.title} · ${curriculumTitle('difficulties',scenario.difficulty||'basic')} · ${curriculumTitle('profiles',scenario.dds_profile||'general')}`,scenario.id));el('lessonScenarios').replaceChildren();scenarioPool=scenarios;try{curriculum=await api('/api/v1/instructor/curriculum');}catch{curriculum=fallbackCurriculum;}addLessonCurriculumControls();addLessonTransportControls();try{routingServices=(await api('/api/v1/instructor/routing/catalog')).services||[];}catch{routingServices=[];}const categories=await api('/api/v1/instructor/categories');for(const c of categories)el('lessonCategories').add(new Option(c.title,c.id));refreshLessonChoices();await Promise.all([loadAssignments(),loadSessions(),loadLessons()]);teacherReady=true;updateLessonSummary();}
// Кабинет ученика: сначала то, что можно делать сейчас, затем свободная
// тренировка; завершённые занятия свёрнуты, чтобы не теряться в длинном списке.
async function loadStudent(){
 const assignments=await api('/api/v1/student/assignments'),lessons=await api('/api/v1/student/lessons');
 try{curriculum=await api('/api/v1/student/curriculum');}catch{curriculum=fallbackCurriculum;}
 const states={planned:'Ожидание старта',running:'Идёт',stopping:'Завершается',finished:'Завершено'};
 const meta=item=>[curriculumTitle('difficulties',item.difficulty||'basic'),curriculumTitle('profiles',item.dds_profile||'general')].join(' · ');
 const training=l=>/обучение|подсказк/i.test(l.title);
 const active=lessons.filter(l=>l.state!=='finished').sort((a,b)=>training(b)-training(a)),done=lessons.filter(l=>l.state==='finished');
 const lessonCard=(l,open)=>{const card=node('article',undefined,'card'+(training(l)&&open?' card-featured':''));
  card.append(node('h3',l.title),node('p',`${states[l.state]||l.state} · выполнено ${l.completed} из ${l.cards_per_student??'∞'}`),node('p',meta(l),'muted'));
  if(training(l)&&open)card.append(node('p','Пошаговое обучение: наставник на экране подскажет, что выбрать и какой текст написать на каждом шаге.'));
  if(open&&l.state==='running'){const go=node('a','Начать работу','button primary');go.href='/?lesson='+encodeURIComponent(l.id);card.append(go);}
  else card.append(node('span',states[l.state]||l.state,'badge'));
  return card;};
 const section=(title,hint,items)=>{const box=node('section',undefined,'student-section');box.append(node('h2',title));if(hint)box.append(node('p',hint,'muted'));const grid=node('div',undefined,'cards student-grid');items.forEach(i=>grid.append(i));box.append(grid);return box;};
 const container=el('studentAssignments');container.replaceChildren();
 if(!assignments.length&&!lessons.length){empty(container,'Активных заданий пока нет');return;}
 if(active.length)container.append(section('Идут сейчас','Занятия группы, которые ведёт преподаватель.',active.map(l=>lessonCard(l,true))));
 else container.append(section('Идут сейчас','Сейчас нет запущенных занятий. Преподаватель запустит занятие, и оно появится здесь.',[]));
 if(assignments.length)container.append(section('Свободная тренировка','Задания для самостоятельной практики в рабочем месте.',assignments.map(item=>{const card=node('article',undefined,'card');
  card.append(node('h3',item.title),node('p',item.scenario_title||item.scenario_id),node('p',meta(item),'muted'));
  if(item.learning_objectives)card.append(node('p','Учебные цели: '+item.learning_objectives,'objectives'));
  card.append(node('span','Доступно','badge'));return card;})));
 if(done.length){const more=node('details',undefined,'student-section student-done');more.append(node('summary',`Завершённые занятия (${done.length})`));const grid=node('div',undefined,'cards student-grid');done.forEach(l=>grid.append(lessonCard(l,false)));more.append(grid);container.append(more);}
}

el('logout').addEventListener('click',()=>act(async()=>{await api('/api/v1/auth/logout','POST');sessionStorage.removeItem('studentSession');localStorage.removeItem('studentSession');location.replace('/login');}));
el('userForm').addEventListener('submit',event=>{event.preventDefault();act(async()=>{await api('/api/v1/admin/users','POST',{username:el('newUsername').value,display_name:el('newDisplayName').value,role:el('newRole').value,password:el('newPassword').value});event.target.reset();await loadAdmin();},'Пользователь создан');});
el('groupForm').addEventListener('submit',event=>{event.preventDefault();act(async()=>{await api('/api/v1/instructor/groups','POST',{title:el('groupTitle').value});event.target.reset();await loadGroups();},'Группа создана');});
el('assignmentForm').addEventListener('submit',event=>{event.preventDefault();act(async()=>{await api('/api/v1/instructor/assignments','POST',{group_id:el('assignmentGroup').value,scenario_id:el('scenarioId').value,title:el('assignmentTitle').value});event.target.reset();await loadAssignments();},'Задание создано');});
el('closeSession').addEventListener('click',()=>el('sessionDialog').close());
el('sessionDialog').addEventListener('close',()=>{openSessionId=null;sessionRequestVersion++;});
el('refreshSessions').addEventListener('click',()=>act(async()=>{await loadSessions();await loadLessons();}));

act(async()=>{me=await api('/api/v1/auth/me');el('identity').textContent=`${me.display_name} · ${roleNames[me.role]||me.role}`;await window.trainerMfa?.render(me);if(me.mfa?.required&&!me.mfa?.enabled)return;const panel=el(`${me.role}Panel`);if(!panel){throw Error('Для этой роли кабинет не настроен');}panel.hidden=false;if(me.role==='admin')await loadAdmin();else if(me.role==='teacher')await loadTeacher();else await loadStudent();});
// Сводка занятия: что получит каждый обучающийся при текущих настройках.
function updateLessonSummary(){
 const target=el('lessonSummary');if(!target)return;const group=currentLessonGroup(),mode=el('lessonMode').value;
 const people=group?group.member_ids.length:0,count=el('lessonUnlimited').checked?'карточки до остановки преподавателем':`${el('lessonCount').value||0} карт.`;
 const pool=mode==='actions'?el('lessonGenerated').selectedOptions.length+el('lessonSources').selectedOptions.length:mode==='fill'?el('lessonScenarios').selectedOptions.length:el('lessonScenarios').selectedOptions.length+el('lessonGenerated').selectedOptions.length+el('lessonSources').selectedOptions.length;
 const filters=[el('lessonDifficulty')?.value&&el('lessonDifficulty').selectedOptions[0].text,el('lessonProfile')?.value&&el('lessonProfile').selectedOptions[0].text,el('lessonCategories').selectedOptions.length&&`категорий: ${el('lessonCategories').selectedOptions.length}`].filter(Boolean);
 const modes={actions:'работа ДДС с готовыми карточками',fill:'приём вызова 112 и заполнение карточки',mixed:'смешанное занятие'};
 const parts=[group?`Группа «${group.title}»: ${people} обучающ.`:'Выберите группу',`каждому — ${count}, одновременно ${el('lessonParallel').value||1}`,modes[mode],
  pool?`выбрано источников: ${pool}`:filters.length?`подбор по фильтрам (${filters.join(', ')})`:'выберите карточки или фильтры',
  el('lessonTransport')?.value==='sip'?'канал: IP-телефон':'канал: текст',`норматив реакции ${el('lessonNorm').value||30} с`,
  el('lessonPass').value!==''?`порог зачёта ${el('lessonPass').value} б.`:'порог — по правилам сценария',el('lessonPractice')?.checked?'с подсказками':'без подсказок'];
 target.textContent='Итог: '+parts.join(' · ')+'.';target.classList.toggle('warning',!group||!people);
}
el('lessonForm').addEventListener('input',updateLessonSummary);el('lessonForm').addEventListener('change',updateLessonSummary);
