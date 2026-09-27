'use strict';
const $ = id => document.getElementById(id);
document.querySelector('.address-block').append($('mapControls').content.cloneNode(true));
const fields = [...document.querySelectorAll('[data-field]')];
let sessions = [], current = null, dirty = false, viewing = false, sending = false, toastTimer, transcriptKey = '';
if(typeof BroadcastChannel!=='undefined'){
 const geocoderChannel=new BroadcastChannel('trainer112-geocoder');
 geocoderChannel.onmessage=({data})=>{
  if(data?.type!=='coordinates'||data.sid!==current?.id||current.status==='Завершена'||current.card_locked)return;
  if(!Number.isFinite(data.latitude)||!Number.isFinite(data.longitude)||Math.abs(data.latitude)>90||Math.abs(data.longitude)>180)return;
  let dialog=$('coordinateConfirm');if(dialog)dialog.remove();
  dialog=element('dialog');dialog.id='coordinateConfirm';
  const message=element('p',`Использовать координаты адреса «${String(data.label).slice(0,200)}»? Текст адреса не изменится.`);
  const apply=element('button','Применить координаты'),cancel=element('button','Отмена');
  cancel.onclick=()=>dialog.close();
  apply.onclick=()=>{
   if(data.sid!==current?.id||current.card_locked)return dialog.close();
   viewing=false;current.card.latitude=data.latitude;current.card.longitude=data.longitude;
   for(const input of document.querySelectorAll('[data-field="latitude"],[data-field="longitude"]')){input.value=data[input.dataset.field];input.disabled=false;}
   markDirty();$('save').hidden=false;dialog.close();$('mapDialog')?.close();notify('Координаты изменены в черновике. Сохраните карточку.');
  };
  dialog.append(message,apply,cancel);document.body.append(dialog);dialog.showModal();
 };
}
// Номер рабочего места оператор вводит при входе. Назначение преподавателя
// перекрывает его на сервере — здесь только передаём то, что ввели.
function workstation(){try{return (localStorage.getItem('workstation')||'').slice(0,80);}catch{return '';}}
const expandedJournalRows = new Set();
const attemptedLessonCalls = new Set(), pendingCalls = new Set();
const pendingTextMessages=new Map();
let connectionLost=!navigator.onLine,recoveryBusy=false,healthText='Подключение…';
function draftKey(sid){return studentUserId&&sid?`draft:${studentUserId}:${sid}`:'';}
function storedDraft(sid){
 const key=draftKey(sid);if(!key)return null;
 try{const value=JSON.parse(sessionStorage.getItem(key)||'null');return value?.card||value||null;}catch{sessionStorage.removeItem(key);return null;}
}
function persistDraft(card=readCard()){
 const key=draftKey(current?.id);if(!key)return;
 try{sessionStorage.setItem(key,JSON.stringify({card,revision:current.revision,saved_at:new Date().toISOString()}));current.unsaved_draft=card;}catch{notify('Не удалось сохранить черновик в этой вкладке. Экспортируйте его до перезагрузки.',true);}
}
function clearDraft(sid){const key=draftKey(sid);if(key)sessionStorage.removeItem(key);if(current?.id===sid)delete current.unsaved_draft;}
function attachStoredDraft(session){const draft=storedDraft(session.id);if(draft)session.unsaved_draft=draft;return draft;}
function messageKey(sid){return studentUserId&&sid?`pendingMessage:${studentUserId}:${sid}`:'';}
function storedMessage(sid){if(pendingTextMessages.has(sid))return pendingTextMessages.get(sid);const key=messageKey(sid);if(!key)return null;try{const value=JSON.parse(sessionStorage.getItem(key)||'null');if(value)pendingTextMessages.set(sid,value);return value;}catch{sessionStorage.removeItem(key);return null;}}
function storeMessage(sid,value){pendingTextMessages.set(sid,value);const key=messageKey(sid);if(key)try{sessionStorage.setItem(key,JSON.stringify(value));}catch{notify('Реплика готова к ручному повтору только до перезагрузки вкладки.',true);}}
function clearMessage(sid){pendingTextMessages.delete(sid);const key=messageKey(sid);if(key)sessionStorage.removeItem(key);}
function renderConnection(){
 if(typeof renderPresence==='function')renderPresence();
 $('health').textContent=!navigator.onLine?'○ Браузер без сети':connectionLost?'○ Нет связи с Backend':healthText;
}
function noteDisconnected(){connectionLost=true;renderConnection();}
function noteConnected(){const recovered=connectionLost;connectionLost=false;renderConnection();if(recovered&&!recoveryBusy)queueMicrotask(()=>guarded(recoverCurrentSession));}
async function startSipCall(){
 const session=current;
 if(!session||session.status==='Завершена'||session.call_id||pendingCalls.has(session.id))return;
 pendingCalls.add(session.id);$('sipCall').disabled=true;
 try{
  const result=await api(`student/sessions/${session.id}/call`,'POST');
  if(current?.id===session.id&&current.status!=='Завершена'){
   current.call_id=result.call_id;
   $('callStatus').textContent='Примите звонок на '+(current.sip_extension||'201');
  }
 }catch(error){if(current?.id===session.id){$('callStatus').textContent='Звонок не подключён — нажмите кнопку вызова для повтора';notify(error.message,true);}}
 finally{pendingCalls.delete(session.id);if(current?.id===session.id)$('sipCall').disabled=current.status==='Завершена'||!!current.call_id;}
}
const address = [['country','Страна:',2],['region','Субъект:',2],['city','Населённый пункт:',2],['object','Объект:',3],['district','Округ:',1],['area','Район:',2],['street','Улица:',3],['house','Дом/Вл:',1],['building','Корпус:',2],['structure','Стр/соор:',1],['apartment','Квартира/офис:',1],['entrance','Подъезд:',1],['floor','Этаж:',1],['code','Код:',2]];
for (const [key, label, size] of address) {
 const el = document.createElement('label'); el.textContent = label; el.className = size === 3 ? 'medium' : size === 1 ? 'short' : '';
 const input = document.createElement('input'); input.dataset.field = key; input.maxLength = ['house','building','structure','apartment','entrance','floor','code'].includes(key) ? 40 : key === 'object' || key === 'street' ? 200 : 100; el.append(input); $('addressFields').append(el); fields.push(input);
}
let serviceNames = [];
let routingView = null, routingFingerprint = '', routingRequest = 0;
const pendingReviews = new Set();
let catalog = null, routingCatalog = null, assignments = [], savedServices = new Set(), pendingServiceAction = null, pendingNotification = null;
let studentUserId='',activeLessonId='',lessonFlowBusy=false,availableLessons=[],pendingLessonSessionId='';
function chooseLesson(id){activeLessonId=id;if(studentUserId){if(id)sessionStorage.setItem('activeLesson:'+studentUserId,id);else sessionStorage.removeItem('activeLesson:'+studentUserId);}}
function enterLessonCard(data,allowAutomaticSip=true){
 if(!allowAutomaticSip)attemptedLessonCalls.add(data.id);
 current=data;attachStoredDraft(current);dirty=false;viewing=current.revision>0;transcriptKey='';localStorage.setItem('studentSession',current.id);
 for(const dialog of document.querySelectorAll('dialog[open]'))dialog.close();
 renderCard();if(current.exercise_mode!=='actions'&&current.status!=='Завершена')$('dialogueDialog').showModal();
}
function receiveRemoteCompletion(data){
 const draft=dirty?readCard():null;
 if(draft)persistDraft(draft);
 current=data;attachStoredDraft(current);attemptedLessonCalls.add(data.id);
 dirty=false;viewing=true;window.speechSynthesis?.cancel();$('dialogueDialog').close();renderCard();showAudit();
 notify(draft?'Преподаватель завершил карточку. Несохранённый черновик доступен в JSON-экспорте.':'Карточка завершена преподавателем',true);
}
// Панель занятия должна объяснять себя: что за карточки прилетают, по какому
// каналу идёт разговор и что будет после завершения. Без этого «войти в
// занятие» выглядит как вход в неизвестность.
function lessonExplanation(lesson){
 const mode=lesson.mode==='actions'
  ? 'готовые карточки ДДС: поля уже заполнены, нужны действия'
  : lesson.mode==='fill'
  ? 'полный цикл 112: карточку заполняете вы со слов заявителя'
  : 'смешанное: и готовые карточки, и приём вызова';
 const channel=lesson.mode==='actions'
  ? lesson.sip_extension?`доклад голосом или текстом, ваш номер ${lesson.sip_extension}`:'доклад текстом; SIP-номер не назначен'
  : lesson.transport==='sip'?`разговор по телефону, ваш номер ${lesson.sip_extension||'не назначен'}`:'разговор текстом в окне «Диалог»';
 const next=lesson.state==='running'&&!lesson.exhausted
  ? 'после завершения карточки сразу выдаётся следующая'
  : '';
 return [mode, channel, next].filter(Boolean).join(' · ');
}
async function refreshLessonFlow(){
 if(!studentUserId||lessonFlowBusy||document.hidden)return;lessonFlowBusy=true;
 try{
  availableLessons=await api('student/lessons');const select=$('activeLesson'),selected=select.value||activeLessonId;
  select.replaceChildren(new Option('Выберите занятие',''));const labels={planned:'ожидание старта',running:'идёт',stopping:'завершается',finished:'завершено'};
  for(const l of availableLessons)select.add(new Option(`${l.title} · ${labels[l.state]} · ${l.completed}/${l.cards_per_student??'∞'}`,l.id));select.value=selected;
  const lesson=availableLessons.find(l=>l.id===activeLessonId);
  // «Делай как я»: шаг, выбранный преподавателем, показывается сразу.
  if(window.applyGuidedStep)window.applyGuidedStep(lesson?lesson.guided_step:null);
  // Кнопка появляется, только если преподаватель разрешил переключение.
  const switchable=!!lesson&&lesson.state==='running'&&lesson.allow_mode_switch;
  $('modeSwitch').hidden=!switchable;
  if(switchable)$('modeSwitch').textContent=studentMode==='fill'
   ? 'Обычный режим: работа диспетчера ДДС'
   : 'Расширенный режим: полный цикл 112';
  if(!lesson){$('lessonState').textContent=activeLessonId?'Занятие недоступно'
   :'Занятие — это серия карточек от преподавателя. Для одиночной тренировки занятие не нужно: нажмите «создать новую карточку».';return;}
  $('lessonState').textContent=`${lesson.title}: ${labels[lesson.state]} · выполнено ${lesson.completed}${lesson.cards_per_student===null?'':` из ${lesson.cards_per_student}`} · ${lessonExplanation(lesson)}`;
  if(lesson.state!=='running'){
   if(current?.lesson_id===lesson.id&&current.status!=='Завершена'){
    const data=await api(`student/sessions/${current.id}`);if(data.status==='Завершена')receiveRemoteCompletion(data);
   }
   return;
  }
  if(lesson.exhausted){$('lessonState').textContent+=' · все карточки выполнены, ожидайте завершения преподавателем';return;}
  if(sending||dirty)return;
  if(current&&current.status!=='Завершена'&&current.lesson_id!==lesson.id){$('lessonState').textContent+=' · сначала завершите открытую карточку';return;}
  if(current?.lesson_id===lesson.id&&current.status!=='Завершена')return;
  if(pendingLessonSessionId&&lesson.active_session_ids?.includes(pendingLessonSessionId))return;
  if(!catalog)await loadClassifier();
  const after=current?.lesson_id===lesson.id?current.id:null;
   const data=await api(`student/lessons/${lesson.id}/next`,'POST',{...(after?{after_session_id:after}:{}),...nextMode(),workstation:workstation()});
   if(data.exercise_mode==='actions'&&!data.opened_at){
    if(pendingLessonSessionId!==data.id){pendingLessonSessionId=data.id;await loadSessions();$('journalPanel').hidden=false;$('cardPanel').hidden=true;notify(`Поступила карточка № ${data.number}. Откройте её в журнале.`);}
   }else enterLessonCard(data,lesson.active_session_id!==data.id);
 }catch(error){$('lessonState').textContent=error.message+' · повторная проверка автоматически';}
 finally{lessonFlowBusy=false;}
}
$('joinLesson').onclick=()=>guarded(async()=>{if(!confirmLeave())return;const id=$('activeLesson').value;if(!id)throw Error('Выберите занятие');chooseLesson(id);await refreshLessonFlow();});
$('leaveLesson').onclick=()=>{chooseLesson('');$('lessonState').textContent='Автоматическая выдача выключена. Результаты сохранены; преподаватель по-прежнему может завершить занятие.';};
setInterval(refreshLessonFlow,2000);
async function loadClassifier() {
 const loaded=await Promise.all([api('student/classifier'),api('student/routing/catalog')]);catalog=loaded[0];routingCatalog=loaded[1];serviceNames=routingCatalog.services;
 const select=$('incidentType'); select.removeAttribute('data-field');
 const index=fields.indexOf(select);if(index>=0)fields.splice(index,1);
 select.replaceChildren(new Option('добавить тип происшествия',''));
 for(const group of catalog.groups)select.add(new Option(group.title,group.id));
 for(const old of $('routingFlags').querySelectorAll('[data-field]')){const position=fields.indexOf(old);if(position>=0)fields.splice(position,1);} $('routingFlags').querySelectorAll('label').forEach(node=>node.remove());
 for(const flag of routingCatalog.flags){if(fields.some(input=>input.dataset.field===flag.id))continue;const label=element('label'),input=element('input');input.type='checkbox';input.dataset.field=flag.id;input.addEventListener('input',()=>{markDirty();guarded(refreshRouting);});fields.push(input);label.append(input,document.createTextNode(flag.label));$('routingFlags').append(label);}
 $('filterService').replaceChildren(new Option('все службы',''));for(const service of serviceNames)$('filterService').add(new Option(service,service));
}
function notify(text, error = false) { clearTimeout(toastTimer); $('notice').textContent = text; $('notice').className = error ? 'error' : ''; $('notice').hidden = false; toastTimer = setTimeout(() => $('notice').hidden = true, error ? 10000 : 4500); }
async function api(path, method = 'GET', body) {
 let r;try{r=await fetch('/api/v1/' + path, {method, headers:{'Content-Type':'application/json','X-Voice-UI':'1'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});}catch{noteDisconnected();throw Error('Нет связи с Backend. Данные не отправлены; повторите действие после восстановления связи.');}
 noteConnected();
 if(r.status===401){location.replace('/login');throw Error('Войдите в систему');}
 let data; try { data = await r.json(); } catch { throw Error('Сервер вернул некорректный ответ'); }
 if (!r.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Проверьте заполнение полей');
 if(path==='auth/me')document.querySelector('.console-meta small').textContent=(data.display_name||'Оператор')+' · смена';
 return data;
}
async function guarded(fn) { try { return await fn(); } catch(e) { notify(e.message, true); } }
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
function formatted(value) { return new Date(value).toLocaleString('ru-RU'); }
function addressText(c) { return [c.country,c.city,c.district,c.area,c.street,c.house && 'д. '+c.house,c.building && 'корп. '+c.building,c.apartment && 'кв. '+c.apartment].filter(Boolean).join(', '); }
function markDirty() { dirty = true; routingView=null; $('applyRouting').disabled=true;for(const id of ['processed','openForward','openBriefing','openNotification','openLinks','printCard'])$(id).disabled=true; if($('serviceDialog').open)$('routingPreview').replaceChildren(element('p','Признаки или карточка изменены. Обновите подбор.')); $('saveState').textContent = 'Не сохранено · черновик в этой вкладке'; $('cardPanel').classList.add('dirty'); $('addressSummary').textContent = addressText(readCard()); $('characters').textContent = $('description').value.length + ' / 1999'; persistDraft();$('restoreDraft').hidden=true; }
function readCard() { const card = {...(current?.card || {})}; for (const input of fields) card[input.dataset.field] = input.type === 'checkbox' ? input.checked : input.type==='number' ? (input.value===''?null:Number(input.value)) : input.value; return card; }
for (const input of fields) input.addEventListener('input', () => {
 markDirty();
 if(routingCatalog?.flags.some(flag=>flag.id===input.dataset.field))guarded(refreshRouting);
});
function confirmLeave() { return !dirty || confirm('Изменения карточки не сохранены. Продолжить без сохранения?'); }
function journal() { if (!confirmLeave()) return; dirty = false; $('cardPanel').hidden = true; window.updateDdsCoach?.(current); $('journalPanel').hidden = false; $('journal').classList.add('selected'); $('openCard').classList.remove('selected'); guarded(loadSessions); }
async function loadSessions() {
 const next=[];for(let offset=0;offset<10000;offset+=200){const batch=await api(`student/sessions?limit=200&offset=${offset}`);next.push(...batch);if(batch.length<200)break;if(next.length===10000)notify('Загружены первые 10 000 карточек; более старые записи не загружены.',true);}
 if(JSON.stringify(next)!==JSON.stringify(sessions)){sessions=next;const selected=$('filterIncidentStatus').value,statuses=[...new Set(sessions.map(s=>s.incident_status).filter(Boolean))];$('filterIncidentStatus').replaceChildren(new Option('все статусы',''));for(const value of statuses)$('filterIncidentStatus').add(new Option(value,value));$('filterIncidentStatus').value=selected;renderRows();}
}
function includes(value,query){return !query||String(value||'').toLocaleLowerCase('ru-RU').includes(query.toLocaleLowerCase('ru-RU'));}
function localDate(value){const date=new Date(value),pad=n=>String(n).padStart(2,'0');return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;}
function renderRows() {
 const query = $('search').value.trim().toLowerCase(), status = $('statusFilter').value;
 const districts=$('filterDistrict').value.split(',').map(v=>v.trim()).filter(Boolean),dateFrom=$('filterDateFrom').value,dateTo=$('filterDateTo').value,timeFrom=$('filterTimeFrom').value,timeTo=$('filterTimeTo').value;
 const list = sessions.filter(s => {
  const c=s.card,date=localDate(s.created_at),created=new Date(s.created_at),time=`${String(created.getHours()).padStart(2,'0')}:${String(created.getMinutes()).padStart(2,'0')}`,caller=`${c.caller_name||''} ${c.phone||''} ${c.supplied_phone||''} ${c.scene_phone||''}`,features=(c.classifier_features||[]).join(' ');
  // Журнал накапливает всю смену, как в настоящем АРМ. Флажок сужает его до
  // карточек выбранного занятия: после смены занятия прежние мешают.
  if($('lessonScope').checked&&activeLessonId&&s.lesson_id!==activeLessonId)return false;
  return (!status||s.status===status)&&(!query||`${s.number} ${c.incident_type} ${addressText(c)} ${c.description}`.toLowerCase().includes(query))&&(!dateFrom||date>=dateFrom)&&(!dateTo||date<=dateTo)&&(!timeFrom||time>=timeFrom)&&(!timeTo||time<=timeTo)&&includes(c.incident_type,$('filterIncidentType').value)&&includes(features,$('filterFeatures').value)&&includes(`${addressText(c)} ${c.address_note||''}`,$('filterAddress').value)&&(!districts.length||districts.some(v=>includes(c.district,v)))&&includes(c.area,$('filterArea').value)&&includes(c.region,$('filterRegion').value)&&includes(c.address_note,$('filterAddressNote').value)&&includes(c.description,$('filterDescription').value)&&includes(s.number,$('filterCardNumber').value)&&(!$('filterService').value||(c.services||[]).includes($('filterService').value))&&(!$('filterTransport').value||s.transport===$('filterTransport').value)&&includes(caller,$('filterCaller').value)&&(!$('filterIncidentStatus').value||s.incident_status===$('filterIncidentStatus').value);
 });
 $('rows').replaceChildren(); $('empty').hidden = sessions.length > 0; $('count').textContent = `Записей: ${list.length}`;
 for (const s of list) {
  const row = document.createElement('tr'),detailId=`journal-detail-${s.id}`,expanded=expandedJournalRows.has(s.id);row.tabIndex=0;row.setAttribute('aria-label', `Происшествие ${s.number}`);
  const awaitingReceipt=s.exercise_mode==='actions'&&s.owner_service&&!s.opened_at&&s.status!=='Завершена';
  const receiptSeconds=awaitingReceipt?Math.ceil(30-(Date.now()-new Date(s.created_at).getTime())/1000):null;
  const receiptLabel=awaitingReceipt?(receiptSeconds>0?` · ${receiptSeconds} с на открытие`:' · открытие просрочено'):'';
  const toggle=element('button',expanded?'⌃':'⌄','row-toggle');toggle.type='button';toggle.setAttribute('aria-expanded',String(expanded));toggle.setAttribute('aria-controls',detailId);toggle.setAttribute('aria-label',`${expanded?'Скрыть':'Показать'} сведения карточки ${s.number}`);const toggleCell=element('td');toggleCell.append(toggle);row.append(toggleCell);
  const registration=s.registration||{},values=[(s.linked_cards||[]).length||'',s.card.bookmarked?'▮':'',s.card.important?'!':'',s.card.emergency?'ϟ':'',registration.operator||'—',registration.workstation||'—',s.number,new Date(s.created_at).toLocaleDateString('ru-RU'),new Date(s.created_at).toLocaleTimeString('ru-RU'),s.card.incident_type||'Не классифицировано',s.card.injured?'Да':'Нет',`${s.incident_status||'Новая'} / ${s.status}${receiptLabel}`,addressText(s.card)];
  values.forEach((v,index)=>{const cell=element('td',v);if(index===11&&['Не оповещено','Отказ','Не завершено'].includes(s.incident_status))cell.classList.add('incident-status-abnormal');row.append(cell);});
  const view=element('button','▣','row-view');view.type='button';view.setAttribute('aria-label',`Открыть карточку ${s.number}`);const viewCell=element('td');viewCell.append(view);row.append(viewCell);$('rows').append(row);
  const description=element('tr',undefined,'description-row incident-detail-row');description.id=detailId;description.hidden=!expanded;const td=element('td');td.colSpan=15;
  const detail=element('div',undefined,'incident-inline-detail'),services=(s.card.services||[]).map(service=>{const state=s.service_states?.[service]||{};return `${service}: ${state.status||'Добавлена'}${state.at?' · '+formatted(state.at):''}`;});
  const facts=[['Службы',services.join('; ')||'не выбраны'],['Заявитель',[s.card.caller_name,s.card.phone,s.card.supplied_phone,s.card.scene_phone].filter(Boolean).join(' · ')||'не указан'],['Информация',s.card.description||'не внесена'],['Признаки',(s.card.classifier_features||[]).join(' · ')||'не выбраны'],['Отработано',s.processed_at?formatted(s.processed_at):'нет'],['Описательный адрес',s.card.address_note||'не указан']];
  for(const [label,value] of facts){const line=element('p');line.append(element('strong',label+': '),document.createTextNode(value));detail.append(line);}td.append(detail);description.append(td);$('rows').append(description);
  const setExpanded=()=>{const next=!expandedJournalRows.has(s.id);if(next)expandedJournalRows.add(s.id);else expandedJournalRows.delete(s.id);description.hidden=!next;toggle.textContent=next?'⌃':'⌄';toggle.setAttribute('aria-expanded',String(next));toggle.setAttribute('aria-label',`${next?'Скрыть':'Показать'} сведения карточки ${s.number}`);};
  toggle.onclick=event=>{event.stopPropagation();setExpanded();};view.onclick=event=>{event.stopPropagation();guarded(()=>openSession(s.id));};row.onclick=()=>guarded(()=>openSession(s.id));row.onkeydown=event=>{if(event.key==='Enter'&&event.target===row)row.click();};
 }
 if (!list.length && sessions.length) { const tr=element('tr');const td=element('td','По заданным параметрам происшествий нет');td.colSpan=15;tr.append(td);$('rows').append(tr); }
}
async function openSession(id) { if (!confirmLeave()) return; const incoming=sessions.find(item=>item.id===id);current=incoming?.exercise_mode==='actions'&&incoming.status!=='Завершена'?await api(`student/sessions/${id}/open`,'POST',{}):await api('student/sessions/'+id);if(!incoming&&current.exercise_mode==='actions'&&current.status!=='Завершена')current=await api(`student/sessions/${id}/open`,'POST',{});if(!catalog)await loadClassifier();pendingLessonSessionId='';attachStoredDraft(current);attemptedLessonCalls.add(id);transcriptKey = ''; dirty = false; viewing = current.revision > 0; localStorage.setItem('studentSession',id); renderCard(); }
function renderCard() {
 $('journalPanel').hidden = true; $('cardPanel').hidden = false; $('journal').classList.remove('selected'); $('openCard').classList.add('selected');
  const finished = current.status === 'Завершена', readonly = Boolean(viewing || finished || current.card_locked);
 if(viewing||finished)savedServices=new Set(current.card.services||[]);
 $('cardPanel').classList.toggle('readonly',readonly); $('cardPanel').classList.remove('dirty');
 for (const input of fields) { if(input.type==='checkbox') input.checked=!!current.card[input.dataset.field]; else input.value=current.card[input.dataset.field] ?? ''; input.disabled=readonly||(current.exercise_mode==='actions'&&!(current.dds_editable_fields||[]).includes(input.dataset.field)); }
 for(const [key,short,long] of [['injured','Пострадавшие','Пострадавшие'],['refused','Отказ от скорой','Нет на месте / Отказ от скорой'],['no_access','Заблокированные','Нет доступа / Заблокированные']]){
  const input=fields.find(field=>field.dataset.field===key);
  input.parentElement.replaceChildren(input,document.createTextNode(current.exercise_mode==='actions'?short:long));
 }
 $('registrationInfo').textContent=current.registration?`${current.registration.operator} · ${current.registration.workstation}`:'Регистратор не указан в старой карточке';
 $('cardSeat').textContent=$('registrationInfo').textContent;
 $('cardNumber').textContent='Происшествие '+current.number;
 $('savedAt').textContent=(current.saved_at?'Сохр. ':'Созд. ')+formatted(current.saved_at || current.created_at);
 $('addressSummary').textContent=addressText(current.card); $('characters').textContent=current.card.description.length+' / 1999';
 $('saveState').textContent=finished?'Занятие завершено':current.revision?'Сохранено':'Новая карточка';updateIncidentState();
 $('restoreDraft').hidden=finished||dirty||!current.unsaved_draft;
 $('save').hidden=readonly; $('edit').hidden=!readonly||finished||current.card_locked; $('finish').disabled=finished;$('processed').disabled=finished||!current.revision||!!current.processed_at;
 $('cardView').disabled=readonly||!current.revision;
 $('cardView').classList.toggle('active',readonly);
 $('cardSupplement').hidden=finished||current.card_locked||current.exercise_mode==='actions';
 // Поля карточки 112 ДДС не правит: об ошибке сообщает в 112 (ответ заказчика 27.09.2026).
 if(current.exercise_mode==='actions')$('edit').hidden=true;
 $('cardSupplement').disabled=!readonly;
 $('cardSupplement').classList.toggle('active',!readonly);
 $('clearAddress').disabled=readonly; $('addService').hidden=readonly||current.exercise_mode==='actions'; $('responseSection').hidden=!current.revision;
 $('responseService').disabled=finished; $('responseStatus').disabled=finished; $('responseOrderNumber').disabled=finished; $('responseComment').disabled=finished; $('addResponse').disabled=finished;
 $('callStatus').textContent=finished?'завершён':current.transport==='text'?'текстовое обращение':current.call_id?'SIP: проверка связи…':'не подключен';
 $('sipCall').hidden=current.transport!=='sip'; $('sipCall').disabled=finished||!!current.call_id;
 $('messageForm').hidden=current.transport!=='text'||finished; $('speak').disabled=current.transport!=='text';
 // Два режима — два разных рабочих пространства, и это должно быть видно, а не
 // угадываться. В основном режиме ДДС карточка приходит из 112 готовой и полей
 // приёма вызова быть не должно; в расширенном режиме оператор принимает вызов
 // сам, и тогда открывается диалог с заявителем.
 const dds=current.exercise_mode==='actions';
 $('cardPanel').classList.toggle('dds-mode',dds);
 if(!$('cardTutorial')){
  const tutorial=element('button','?','square');tutorial.id='cardTutorial';tutorial.type='button';
  tutorial.title='Обучение работе с АРМ';tutorial.setAttribute('aria-label',tutorial.title);
  tutorial.onclick=()=>window.startOnboarding?.();$('closeCard').before(tutorial);
 }
 if($('cardPanel').dataset.responseSession!==current.id){
  $('cardPanel').classList.remove('response-open');$('cardPanel').dataset.responseSession=current.id;
 }
 // Keep dispatcher tools available without turning the saved card into a table.
 if(!$('closeResponsePanel')){
  const close=element('button','×');close.id='closeResponsePanel';close.type='button';
  close.setAttribute('aria-label','Закрыть реагирование');
  close.onclick=()=>$('cardPanel').classList.remove('response-open');
  $('responseSection').prepend(close);
  const grammar=element('button','Проверить текст');grammar.type='button';grammar.id='checkGrammar';
  grammar.onclick=()=>guarded(async()=>{
   const report=await api('student/grammar/preview','POST',{card:readCard(),comment:$('responseComment').value});
   let dialog=$('grammarDialog');if(!dialog){dialog=element('dialog');dialog.id='grammarDialog';document.body.append(dialog);}
   dialog.replaceChildren(element('h2','Проверка текста'));
   const labels={description:'Описание',caller_name:'Заявитель',street:'Улица',address_note:'Описательный адрес','service_comment:1':'Комментарий службы'};
   for(const item of [...report.mechanical,...(report.suggestions||[])])dialog.append(element('p',`${labels[item.field]||item.field}: ${item.hint}${item.fragment?' — '+item.fragment:''}`));
   if(!report.errors&&!(report.suggestions||[]).length)dialog.append(element('p','Опечаток проверяемых типов не найдено.'));
   const close=element('button','Закрыть');close.type='button';close.onclick=()=>dialog.close();dialog.append(close);dialog.showModal();
  });
  $('responseSection').querySelector('.response-tools').append(grammar);
  const progress=element('button','Уточнить ход работ');progress.id='requestProgress';progress.type='button';
  progress.onclick=()=>guarded(async()=>{
   if(dirty)throw Error('Сначала сохраните изменения карточки');
   const sid=current.id;progress.disabled=true;
   try{const result=await api(`student/sessions/${sid}/progress`,'POST',{});
    if(current?.id!==sid)return;current=result;renderCard();
    let dialog=$('progressDialog');if(!dialog){dialog=element('dialog');dialog.id='progressDialog';document.body.append(dialog);}
    dialog.replaceChildren(element('h2','Старший бригады'),element('p',result.progress_message));
    const close=element('button','Закрыть');close.type='button';close.onclick=()=>dialog.close();dialog.append(close);dialog.showModal();
   }finally{progress.disabled=current?.status==='Завершена'||!current?.assigned_crew;}
  });
  $('responseSection').querySelector('.response-tools').append(progress);
  const tools=element('button','Реагирование');tools.id='cardResponseTools';tools.type='button';
  tools.setAttribute('aria-label','Действия карточки и реагирование');
  tools.onclick=()=>{const panel=$('cardPanel');if(panel.classList.contains('response-compact')){panel.classList.remove('response-compact');panel.classList.add('response-open');}else panel.classList.toggle('response-open');};
  $('closeCard').before(tools);
  const practice=element('button','Практика с подсказками');practice.id='cardPractice';practice.type='button';
  practice.onclick=()=>window.startDdsCoach?.();$('closeCard').before(practice);
 }
 $('cardPractice').hidden=!dds;
 window.updateDdsCoach?.(current);
 $('modeNote').textContent=dds?'АРМ диспетчера ДДС':'Расширенный режим: приём вызова 112';
 $('requestProgress').hidden=!dds;$('requestProgress').disabled=finished||!current.assigned_crew;
 let evidence=$('correctionEvidence');if(!evidence){evidence=element('div');evidence.id='correctionEvidence';}
 $('cardPanel').querySelector('.phone-row').after(evidence);
 evidence.replaceChildren();for(const text of current.correction_evidence||[])evidence.append(element('p','Сведения с места — сверьте с карточкой и при ошибке сообщите в 112: '+text));
 $('conversation').hidden=dds;
 if(dds){$('messageForm').hidden=true;$('speak').disabled=true;$('callStatus').textContent='карточка передана Службой 112';}
 $('openNotification').disabled=finished||!current.revision;$('openForward').hidden=dds;$('openForward').disabled=dds||finished||!current.revision;$('openBriefing').disabled=finished||!current.revision;$('openErrorReport').hidden=!dds;$('openErrorReport').disabled=finished||!current.revision||current.card_locked;$('openLinks').disabled=finished||!current.revision;$('openReminder').disabled=finished;$('printCard').hidden=!current.revision;$('printCard').disabled=false;renderSurvey(); renderServices();renderCrew();renderDialogue();renderServiceHistory();renderNotificationHistory();renderLinkedCards();renderSituationUpdates();tick();
 const pending=storedMessage(current.id);if(pending&&current.transport==='text'&&current.status!=='Завершена'){$('operatorText').value=pending.text;$('send').textContent='Повторить отправку';}
 if(!finished&&current.lesson_id&&current.transport==='sip'&&!current.call_id&&!attemptedLessonCalls.has(current.id)){
  attemptedLessonCalls.add(current.id);startSipCall();
 }
}
// Новая вводная — это изменившаяся обстановка, а не подсказка: диспетчер
// должен её заметить и пересмотреть решение. Поэтому она попадает и в ленту
// карточки, и в уведомление, и её нельзя пропустить незаметно.
const seenUpdates=new Set();
function renderSituationUpdates(){
 const list=current?.situation_updates||[];
 const pending=current?.pending_phone_reports||[];
 const box=$('situationFeed');
 box.replaceChildren();
 box.hidden=!list.length&&!pending.length;
 for(const item of pending){
  const line=element('article',undefined,'situation-update');
  line.append(element('small',`${item.source} · ожидается телефонный доклад`));
  const button=element('button',item.call_id?'Подтвердить услышанный доклад':'Принять телефонный доклад');
  button.type='button';
  button.addEventListener('click',()=>guarded(async()=>{
   button.disabled=true;
   try{
    const action=item.call_id?'confirm':'call';
    const sid=current.id;
    const data=await api(`student/sessions/${sid}/updates/${encodeURIComponent(item.id)}/${action}`,'POST',{});
    if(current?.id!==sid)return;
    Object.assign(current,{pending_phone_reports:data.pending_phone_reports,
     situation_updates:data.situation_updates,events:data.events,
     allowed_service_statuses:data.allowed_service_statuses,
     field_report_calls:data.field_report_calls});
    current.completion_missing=data.completion_missing;
    renderSituationUpdates();renderServices();window.updateDdsCoach?.(current);
    notify(action==='call'?'Примите вызов на учебный IP-телефон. После доклада подтвердите получение.':'Телефонный доклад внесён в карточку.');
   }finally{button.disabled=false;}
  }));
  line.append(button);
  if(item.call_id){
   const retry=element('button','Повторить вызов');retry.type='button';
   retry.addEventListener('click',()=>guarded(async()=>{
    retry.disabled=true;
    try{
     const sid=current.id;
     const data=await api(`student/sessions/${sid}/updates/${encodeURIComponent(item.id)}/call`,'POST',{});
     if(current?.id!==sid)return;
     current.pending_phone_reports=data.pending_phone_reports||[];
     renderSituationUpdates();notify('Проверьте вызов на учебном IP-телефоне.');
    }finally{retry.disabled=false;}
   }));line.append(retry);
  }
  box.append(line);
 }
 for(const item of list){
  const line=element('article',undefined,'situation-update');
  line.append(element('small',`${formatted(item.at)} · ${item.source}`),element('p',item.text));
  if(item.unlocks_status){
   const recorded=current.events.some(e=>e.type==='service.updated'&&e.detail?.service===current.owner_service&&e.detail?.status===item.unlocks_status&&e.at>=item.at);
   line.append(element('small',recorded?'✓ Отражено в статусе своей службы':'Нужно отразить в статусе своей службы и записать существенные сведения в комментарий.'));
  }
  box.append(line);
 }
 box.scrollTop=box.scrollHeight;
 // Панель реагирования перекрывает ленту докладов: дублируем в ней доклад,
 // который ещё не отражён статусом, — ученик видит, что пришло и что делать.
 let latest=$('latestReport');
 if(!latest){latest=element('div');latest.id='latestReport';$('responseSection').querySelector('.response-input')?.before(latest);}
 const open=[...list].reverse().find(item=>item.unlocks_status&&!current.events.some(e=>e.type==='service.updated'&&e.detail?.service===current.owner_service&&e.detail?.status===item.unlocks_status&&e.at>=item.at));
 latest.hidden=!open;latest.replaceChildren();
 if(open)latest.append(element('b',`Доклад с места · ${open.source} · ${formatted(open.at)}`),element('span',open.text),element('div',`Отразите: статус «${open.unlocks_status}» и запишите в комментарий, что сообщили.`));
}
function receiveSituationUpdates(data){
 const list=data.situation_updates||[];
 const oldPending=(current.pending_phone_reports||[]).map(item=>item.id+':'+(item.call_id||'')).join('|');
 const newPending=(data.pending_phone_reports||[]).map(item=>item.id+':'+(item.call_id||'')).join('|');
 if(list.length===(current.situation_updates||[]).length&&oldPending===newPending)return;
 current.situation_updates=list;
 current.pending_phone_reports=data.pending_phone_reports||[];
 current.events=data.events||current.events;
 const selectedStatus=$('responseStatus').value;
 current.allowed_service_statuses=data.allowed_service_statuses||current.allowed_service_statuses;
 populateResponseStatuses();
 if([...$('responseStatus').options].some(option=>option.value===selectedStatus))$('responseStatus').value=selectedStatus;
 renderSituationUpdates();
 for(const item of list){
  if(seenUpdates.has(item.id))continue;
  seenUpdates.add(item.id);
  notify(`Новая вводная от ${item.source}: ${item.text}`);
 }
}
// Разбор решений диспетчера — главное в основном режиме: поля карточки пришли
// заполненными, и оценивать надо не их, а принятые решения.
function renderDdsReview(){
 const review=current?.dds_review;
 if(!review)return;
 const box=$('auditContent');
 box.append(element('h3','Решения диспетчера'));
 box.append(element('p',`Служба: ${review.service}. Выполнено ${review.passed_count} из ${review.total_count}`
  +(review.score_percent===null?'':` · ${review.score_percent}%`)));
 if(review.critical_errors.length){
  const errors=element('p','Критические ошибки: '+review.critical_errors.join('; '));
  errors.className='dds-critical';box.append(errors);
 }
 for(const check of review.checks){
  const mark=check.passed===true?'✓':check.passed===false?'×':'○';
  const line=element('p',`${mark} ${check.label}${check.detail?' — '+check.detail:''}`);
  if(check.passed===false)line.className=check.critical?'dds-critical':'dds-warning';
  box.append(line);
 }
 box.append(element('small',review.note,'subtle'));
}
function updateIncidentState(){$('incidentState').textContent=`Происшествие: ${current.incident_status||'Новая'} · занятие: ${current.status}`;}
$('openMap').onclick=event=>{
 event.preventDefault();if(!current)return;
 if(dirty){notify('Сначала сохраните карточку: карта показывает сохранённые координаты.',true);return;}
 let dialog=$('mapDialog');
 if(!dialog){dialog=element('dialog');dialog.id='mapDialog';dialog.style.cssText='width:min(1100px,95vw);max-width:95vw;padding:12px';const close=element('button','Закрыть карту');close.type='button';close.onclick=()=>dialog.close();const frame=element('iframe');frame.title='Карта происшествия';frame.style.cssText='width:100%;height:75vh;border:0';dialog.append(close,frame);document.body.append(dialog);}
 dialog.querySelector('iframe').src='/map?sid='+encodeURIComponent(current.id);dialog.showModal();
};
addEventListener('message',event=>{if(event.origin===location.origin&&event.source===$('mapDialog')?.querySelector('iframe')?.contentWindow&&event.data?.type==='close-map')$('mapDialog').close();});
// Реальная опросная карта задаёт вопросы, а не нумерует признаки: «где»,
// «что именно», «какие обстоятельства». Уровни классификатора по смыслу
// ложатся на эти три вопроса, и подписи помогают оператору выбирать по слуху,
// а не сопоставлять номера.
const SURVEY_QUESTIONS=['Где произошло','Что именно произошло','Обстоятельства'];
function surveyQuestion(level){return SURVEY_QUESTIONS[level]||`Признак ${level+1}`;}
function renderSurvey() {
 const c=current.card, readonly=viewing||current.status==='Завершена';
 $('incidentType').value=c.classifier_group||'';$('incidentType').disabled=readonly;
 const group=catalog?.groups.find(g=>g.id===c.classifier_group);
 $('surveyTitle').textContent=group?.title||c.incident_type||'Опросная карта';
 $('typeChip').textContent=c.incident_type||'Выберите признаки происшествия';$('surveyOptions').replaceChildren();
 let candidates=(catalog?.records||[]).filter(r=>r.group_id===c.classifier_group);
 const selected=c.classifier_features||[];
 for(let level=0;level<3&&candidates.length;level++){
  const choices=[...new Set(candidates.map(r=>r.features[level]))];
  const row=element('div',undefined,'survey-row'),options=element('div',undefined,'options');row.append(element('span',surveyQuestion(level)),options);
  for(const value of choices){const active=selected.length>level&&selected[level]===value;if(readonly){if(active&&value)options.append(element('span',value,'survey-value'));continue;}const b=element('button',value||'Не задан в источнике',active?'active':'');b.type='button';b.setAttribute('aria-pressed',String(active));b.onclick=()=>{c.classifier_features=selected.slice(0,level).concat(value);c.classifier_id='';c.incident_type='';chooseRecord();markDirty();renderSurvey();guarded(refreshRouting);};options.append(b);}
  $('surveyOptions').append(row);
  if(selected.length<=level)break;
  candidates=candidates.filter(r=>r.features[level]===selected[level]);
 }
 if(selected.length===3&&candidates.length>1){const row=element('div',undefined,'survey-row');row.append(element('span','Итоговый тип'));const choices=element('select');choices.setAttribute('aria-label','Итоговый тип по классификатору');choices.add(new Option('Выберите запись',''));for(const r of candidates)choices.add(new Option(`${r.incident_type} · строка ${r.source_row}`,r.id));choices.value=c.classifier_id||'';choices.disabled=readonly;choices.onchange=()=>{c.classifier_id=choices.value;chooseRecord();markDirty();renderSurvey();};row.append(choices);$('surveyOptions').append(row);}
 $('classification').textContent=c.incident_type||'не определён';
 const note=$('classification').nextElementSibling;
 const record=catalog?.records.find(r=>r.id===c.classifier_id);
 note.textContent=c.classifier_id?`Источник: строка ${record?.source_row}. ${record?.additional_details||''} Подбор основных служб — в списке оповещения.`:c.classifier_group?'Выберите все признаки, чтобы определить итоговый тип.':'Для нового выбора укажите группу. Ранее сохранённый тип остаётся в карточке.';
}
function chooseRecord(){const c=current.card;const matches=catalog.records.filter(r=>r.group_id===c.classifier_group&&r.features.every((v,i)=>c.classifier_features?.[i]===v));const record=matches.find(r=>r.id===c.classifier_id)||(matches.length===1?matches[0]:null);c.classifier_id=record?.id||'';c.incident_type=record?.incident_type||'';c.classifier_version=catalog.version;}
$('incidentType').addEventListener('change',()=>{ Object.assign(current.card,{classifier_group:$('incidentType').value,classifier_features:[],classifier_id:'',classifier_version:catalog.version,incident_type:'',place:'',sign:'',detail:''});renderSurvey();markDirty();guarded(refreshRouting); });
function primaryServices(){
 return routingView?.primary_services||current?.routing?.primary_services||[];
}
function renderServices() {
 if(!window.ddsFooterObserver&&window.ResizeObserver){
  window.ddsFooterObserver=new ResizeObserver(entries=>{
   for(const entry of entries){
    if(entry.target===$('services')){const box=entry.target.getBoundingClientRect();const tiles=[...entry.target.querySelectorAll('.service-tile')];const top=Math.min(box.top,...tiles.map(t=>t.getBoundingClientRect().top));$('cardPanel').style.setProperty('--dds-service-overflow',`${Math.max(0,box.top-top)}px`);}
    else $('cardPanel').style.setProperty('--dds-footer-height',`${entry.target.getBoundingClientRect().height+8}px`);
   }
  });
  window.ddsFooterObserver.observe($('services').parentElement);
  window.ddsFooterObserver.observe($('services'));
 }
 const opened=new Set([...$('services').querySelectorAll('details[open]')].map(item=>item.dataset.service));
 $('services').replaceChildren(); $('responseService').replaceChildren();
 for (const service of current.card.services) {
  const tile=element('details',undefined,'service-tile'),summary=element('summary'),state=current.service_states[service]||{};
  tile.dataset.service=service;tile.open=opened.has(service);
  tile.ontoggle=()=>{if(tile.open)for(const other of $('services').querySelectorAll('details[open]'))if(other!==tile)other.open=false;};
  const name=element('strong',service);
  // Основная служба для типа происшествия подчёркнута двойной линией: в
  // реальном АРМ по этому признаку оператор видит, кто отвечает за вызов.
  if(primaryServices().includes(service)){name.classList.add('primary-service');name.title='Основная служба для этого типа происшествия';}
  summary.append(name);
  const contact=current.card.service_phones?.[service];
  if(contact&&current.exercise_mode!=='actions')summary.append(element('small',`☎ ${contact}`));
  // Своя ДДС выделена: статусы диспетчер ведёт только по ней, остальные
  // службы он видит, но их состояние приходит от них самих.
  if(current.owner_service&&service===current.owner_service){
   tile.classList.add('own-service');
   summary.append(element('span','моя служба','own-badge'));
  }else if(current.owner_service){
   tile.classList.add('foreign-service');
  }
  if(state.source==='vis')summary.append(element('span','ВИС','vis-badge'));
  const stamp=state.at||state.added_at;
  const statusText=current.exercise_mode==='actions'
   ?`${stamp?new Date(stamp).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'})+' ':''}${state.status||'Добавлена'}`
   :state.status||'Добавлена';
  summary.append(element('small',statusText,'service-status'));if(stamp&&current.exercise_mode!=='actions')summary.append(element('small',formatted(stamp)));tile.append(summary);
  const history=current.events.filter(e=>e.type==='service.updated'&&e.detail.service===service),list=element('div',undefined,'service-tile-history');
  if(contact)list.append(element('p',`Телефон: ${contact}`));
  if(!history.length)list.append(element('small','История статусов пока пуста'));
  for(const event of history){const item=element('p');item.append(element('b',event.detail.status),document.createTextNode(` · ${formatted(event.at)}`));if(event.detail.order_number)item.append(element('span',`Наряд: ${event.detail.order_number}`));if(event.detail.comment)item.append(element('span',event.detail.comment));list.append(item);}
  tile.append(list);$('services').append(tile);
  if(current.exercise_mode==='actions'&&service===current.owner_service){
   const edit=element('button','✎','service-edit');edit.type='button';
   edit.setAttribute('aria-label','Реагирование своей службы');
   edit.onclick=event=>{event.preventDefault();event.stopPropagation();const panel=$('cardPanel');panel.classList.add('response-compact');panel.classList.toggle('response-open');};
   summary.append(edit);
  }
  if(!current.owner_service||service===current.owner_service)$('responseService').add(new Option(service,service));
 }
 populateResponseStatuses();
}
function renderCrew(){
 const choices=current.crew_options||[],box=$('crewSection'),selected=current.assigned_crew;
 box.hidden=current.exercise_mode!=='actions'||!choices.length;
 if(box.hidden)return;
 $('crewAssigned').textContent=selected?`${selected.id} · ${selected.leader} · ${selected.phone} · решение: ${selected.decision_by==='leadership'?'руководитель':'диспетчер'}`:'Выберите бригаду после принятия карточки.';
 $('crewSelect').replaceChildren();
 for(const choice of choices)$('crewSelect').add(new Option(`${choice.id} · ${choice.leader} · ${choice.phone}`,choice.id));
 $('crewForm').hidden=!!selected||current.status==='Завершена';
 $('crewForm').querySelector('button').disabled=current.service_states?.[current.owner_service]?.status!=='Принята';
}
$('assignCrew').onclick=()=>{guarded(async()=>{
 if(dirty)throw Error('Сначала сохраните карточку');
 current=await api(`student/sessions/${current.id}/crew`,'POST',{
  message_id:crypto.randomUUID(),crew_id:$('crewSelect').value,
  decision_by:$('crewDecision').value,decision_note:$('crewDecisionNote').value.trim()});
 renderCard();notify('Бригада назначена');
});};
function populateResponseStatuses(){const values=current?.allowed_service_statuses?.[$('responseService').value]||[];
 $('responseHint').textContent=current?.card_locked?'Работа с карточкой завершена.':values.length?'':(current?.owner_service?'Следующий статус откроется, когда служба сообщит о ходе работ.':'');$('responseStatus').replaceChildren();const prompt=new Option('Выберите статус…','');prompt.disabled=true;prompt.selected=true;$('responseStatus').add(prompt);for(const value of values)$('responseStatus').add(new Option(value,value));const terminal=!values.length||current.status==='Завершена';$('responseStatus').disabled=terminal;$('responseOrderNumber').disabled=terminal;$('responseComment').disabled=terminal;$('addResponse').disabled=terminal;updateResponseHint();}
function updateResponseHint(){$('responseComment').placeholder=$('responseStatus').value==='Работы завершены'?(current?.owner_service==='103'&&!current?.assigned_crew?'Укажите результат; без бригады — «Завершение работ без бригады»':'Результат работ: что сделано и какова обстановка'):'Комментарий к статусу (обязателен): что сообщили, что сделано';}
$('responseService').onchange=populateResponseStatuses;$('responseStatus').onchange=updateResponseHint;
function renderServiceHistory() {
 $('serviceHistory').replaceChildren();
 for(const event of current.events.filter(e=>e.type==='service.updated')) {const tr=element('tr');[formatted(event.at),event.detail.service,event.detail.status,event.detail.order_number||'',event.detail.comment].forEach(v=>tr.append(element('td',v)));$('serviceHistory').append(tr);}
}
function renderDialogue() {
 const key=JSON.stringify(current.messages); if(key===transcriptKey)return;transcriptKey=key;
 $('transcript').replaceChildren(); for(const m of current.messages){const el=element('div',undefined,'message '+m.role);el.append(element('small',m.role==='user'?'Оператор':'Заявитель'),document.createTextNode(m.content));$('transcript').append(el);}
 $('transcript').scrollTop=$('transcript').scrollHeight;
 $('dialogueError').textContent=current.provider_error||'';
}
function say(text) {
 if(!$('speak').checked || !window.speechSynthesis || current.transport!=='text')return;
 window.speechSynthesis.cancel();const utterance=new SpeechSynthesisUtterance(text);utterance.lang='ru-RU';const voice=window.speechSynthesis.getVoices().find(v=>v.lang.startsWith('ru'));if(voice)utterance.voice=voice;utterance.rate=1;
 utterance.onerror=()=>notify('Системная озвучка недоступна. Текст реплики сохранён.',true);window.speechSynthesis.speak(utterance);
}
async function saveCard() {
 if(!current || current.status==='Завершена')return false;
 const button=$('save');button.disabled=true;
 try {const sid=current.id;current=await api(`student/sessions/${sid}/card`,'PUT',{revision:current.revision,card:readCard()});clearDraft(sid);dirty=false;viewing=true;renderCard();notify('Карточка сохранена');return true;}finally{button.disabled=false;}
}
async function newSession() {
 if(!confirmLeave())return;
 if(!catalog)await loadClassifier();
assignments=await api('student/assignments');const lessons=await api('student/lessons');assignments.push(...lessons.filter(l=>l.state==='running'&&!l.exhausted).map(l=>({...l,lesson:true,scenario_title:`Серия: ${l.completed}/${l.cards_per_student??'∞'}`})));$('scenario').replaceChildren();assignments.forEach(s=>$('scenario').add(new Option(`${s.title} · ${s.scenario_title} · ${s.difficulty||'basic'} · ${s.dds_profile||'general'}${s.learning_objectives?' · '+s.learning_objectives:''}`,s.id)));$('start').disabled=!assignments.length;if(!assignments.length)$('scenario').add(new Option('Нет назначений — обратитесь к преподавателю',''));selectTransport();$('newDialog').showModal();
}
function selectTransport(){const assignment=assignments.find(a=>a.id===$('scenario').value);$('transport').disabled=!!assignment?.lesson;if(assignment?.lesson)$('transport').value=assignment.transport||'text';}
$('scenario').addEventListener('change',selectTransport);
$('newForm').onsubmit=e=>{e.preventDefault();guarded(async()=>{$('start').disabled=true;try{const assignment=assignments.find(a=>a.id===$('scenario').value);if(!assignment)throw Error('Выберите назначенное занятие');if(assignment.lesson)chooseLesson(assignment.id);else chooseLesson('');current=assignment.lesson?await api(`student/lessons/${assignment.id}/next`,'POST',{...nextMode(),workstation:workstation()}):await api('student/sessions','POST',{scenario_id:assignment.scenario_id,assignment_id:assignment.id,transport:$('transport').value,workstation:workstation()});if(current.exercise_mode==='actions')current=await api(`student/sessions/${current.id}/open`,'POST',{});pendingLessonSessionId='';attachStoredDraft(current);if(assignment.lesson&&assignment.active_session_id===current.id)attemptedLessonCalls.add(current.id);dirty=false;viewing=false;transcriptKey='';localStorage.setItem('studentSession',current.id);renderCard();$('newDialog').close();if(current.exercise_mode!=='actions')$('dialogueDialog').showModal();else notify('Готовая карточка: внесите действия и результаты реагирования служб');}finally{$('start').disabled=!assignments.length;}});};
$('messageForm').onsubmit=e=>{e.preventDefault();if(sending)return;guarded(async()=>{const text=$('operatorText').value.trim();if(!text)return;const sid=current.id,previous=storedMessage(sid),pending=previous?.text===text?previous:{text,message_id:crypto.randomUUID()};storeMessage(sid,pending);sending=true;$('send').disabled=true;$('send').textContent='Заявитель отвечает…';$('dialogueError').textContent='';try{const result=await api(`student/sessions/${sid}/messages`,'POST',pending);if(current?.id!==sid)return;current.messages=result.messages;current.provider_error=result.provider_error;clearMessage(sid);renderDialogue();$('operatorText').value='';if(!result.provider_error)say(result.messages.at(-1)?.content||'');}finally{sending=false;$('send').disabled=false;$('send').textContent=storedMessage(sid)?'Повторить отправку':'Отправить';}});};
// Инструкция оператора: по «Сохранить» система спрашивает подтверждение и
// показывает, каким службам уйдут сведения. Первое сохранение карточки со
// службами и есть их оповещение, поэтому подтверждение спрашивается один раз —
// при регистрации; последующие дополнения сохраняются без него.
$('cardForm').onsubmit=e=>{
 e.preventDefault();
 const services=readCard().services||[];
 if(current?.revision||!services.length){guarded(saveCard);return;}
 const primary=new Set(primaryServices());
 $('notifyServices').replaceChildren();
 for(const name of services){
  const row=element('p',name,primary.has(name)?'primary-service':'');
  if(primary.has(name))row.append(element('span','основная','service-badge'));
  $('notifyServices').append(row);
 }
 $('notifyDialog').showModal();
};
$('confirmNotify').onclick=()=>guarded(async()=>{
 const button=$('confirmNotify');button.disabled=true;
 try{if(await saveCard())$('notifyDialog').close();}finally{button.disabled=false;}
});
$('sendVis').onclick=()=>guarded(async()=>{
 if(!current)throw Error('Откройте карточку');
 if(!current.registered_at)throw Error('Сначала сохраните и зарегистрируйте карточку');
 if(dirty)throw Error('Сначала сохраните изменения карточки');
 if(!confirm('Передать сохранённую карточку в журнал профильной ДДС?'))return;
 const key='vis-pending:'+current.id;
 let message_id=sessionStorage.getItem(key);if(!message_id){message_id=crypto.randomUUID();sessionStorage.setItem(key,message_id);}
 await api(`student/sessions/${current.id}/vis-deliveries`,'POST',{message_id,informational_recipients:[]});
 sessionStorage.removeItem(key);notify('Передача записана. Карточка доступна преподавателю в журнале ДДС.');
});
$('restoreDraft').onclick=()=>{if(!current?.unsaved_draft||current.status==='Завершена')return;current.card=JSON.parse(JSON.stringify(current.unsaved_draft));viewing=false;dirty=true;renderCard();markDirty();notify('Черновик восстановлен. Проверьте поля и сохраните карточку.');};
$('edit').onclick=()=>{viewing=false;renderCard();};
$('cardView').onclick=()=>{if(dirty){notify('Сначала сохраните дополнение карточки.',true);return;}viewing=true;renderCard();};
$('cardSupplement').onclick=()=>$('edit').click();
$('finish').onclick=()=>{if(sending){notify('Дождитесь ответа заявителя');return;}if($('finishError'))$('finishError').textContent='';let list=$('finishChecklist');if(!list){list=element('div');list.id='finishChecklist';$('confirmFinish').before(list);}list.replaceChildren();const missing=current.completion_missing||[];$('confirmFinish').disabled=missing.length>0;$('confirmFinish').title=missing.length?'Сначала выполните перечисленные действия':'';if(missing.length){list.append(element('strong','Перед завершением осталось:'));const ul=element('ul');for(const item of missing)ul.append(element('li',item));list.append(ul);}else if(current.exercise_mode==='actions')list.append(element('p','Завершение отправит действия на оценку. Прослушанный доклад не заменяет статус и комментарий своей службы.'));$('finishDialog').showModal();};
$('confirmFinish').onclick=()=>guarded(async()=>{const button=$('confirmFinish');button.disabled=true;try{if(dirty||!current.revision)await saveCard();current=await api(`student/sessions/${current.id}/finish`,'POST');dirty=false;viewing=true;window.speechSynthesis?.cancel();$('finishDialog').close();renderCard();if(current.lesson_id){chooseLesson(current.lesson_id);await refreshLessonFlow();if(current.status==='Завершена')showAudit();}else showAudit();}catch(error){let message=$('finishError');if(!message){message=element('p');message.id='finishError';message.setAttribute('role','alert');button.before(message);}message.textContent=error.message;throw error;}finally{button.disabled=false;}});
$('processed').onclick=()=>guarded(async()=>{if(!current||current.status==='Завершена'||current.processed_at)return;if(dirty||!current.revision)throw Error('Сначала сохраните карточку');const button=$('processed');button.disabled=true;try{current=await api(`student/sessions/${current.id}/processed`,'POST',{});dirty=false;viewing=true;renderCard();notify('Происшествие отмечено как отработанное. Занятие не завершено.');}finally{button.disabled=!!current.processed_at;}});
function renderServiceChoices(){
 const readonly=viewing||current.status==='Завершена'||current.exercise_mode==='actions';$('serviceChoices').replaceChildren();
 // Подобранные классификатором службы выделяются, как в реальном АРМ: оператор
 // должен видеть, что предложила система, и что он добавил руками.
 const suggested=new Set((routingView?.suggestions||current?.routing?.suggestions||[]).map(item=>item.service));
 const primary=new Set(primaryServices());
 const query=($('serviceSearch')?.value||'').trim().toLowerCase();
 let shown=0;
 for(const name of new Set([...serviceNames,...current.card.services])){
  if(query&&!name.toLowerCase().includes(query))continue;
  shown++;
  const label=element('label');
  if(suggested.has(name))label.classList.add('auto');
  if(primary.has(name))label.classList.add('primary');
  const input=element('input');input.type='checkbox';input.checked=current.card.services.includes(name);
  input.disabled=readonly||savedServices.has(name);
  input.onchange=()=>{if(input.checked&&current.card.services.length>=100){input.checked=false;notify('В карточке допускается не более 100 служб',true);return;}current.card.services=input.checked?[...current.card.services,name]:current.card.services.filter(s=>s!==name);markDirty();renderServices();};
  label.append(input,document.createTextNode(name));
  if(primary.has(name))label.append(element('span','основная','service-badge'));
  else if(suggested.has(name))label.append(element('span','подобрана','service-badge'));
  $('serviceChoices').append(label);
 }
 if(!shown)$('serviceChoices').append(element('p','По запросу служб не найдено.','subtle'));
}
function renderRouting(result){
 const panel=$('routingPreview');panel.replaceChildren();
 if(!result){panel.append(element('p','Сохранённого подбора нет.'));return;}
 panel.append(element('p',`Правила ${result.rules_version} · ${result.source_row?'строка '+result.source_row:'классификация не выбрана'}`,'subtle'));
 for(const suggestion of result.suggestions){const row=element('article');row.append(element('strong',suggestion.service));for(const m of suggestion.mappings)row.append(element('div',`${m.incident_type} (${m.cell})`));panel.append(row);}
 if(!result.suggestions.length)panel.append(element('p','По поддерживаемым веткам службы не предложены.'));
 for(const item of result.excluded)panel.append(element('p',`${item.service}: ${item.reason} (${item.cell})`,'subtle'));
 for(const item of result.unresolved||[])panel.append(element('p',`Требует уточнения — ${item.service}: ${item.reason}; источник «${item.source_value}» (${item.cell}). Служба не добавлена.`,'routing-warning'));
 for(const warning of result.warnings)panel.append(element('p',warning,'routing-warning'));
}
async function refreshRouting(){
 if(current.exercise_mode==='actions')return;
 const request=++routingRequest,sid=current.id,card=readCard(),fingerprint=JSON.stringify(card);
 routingView=null;$('applyRouting').disabled=true;$('routingPreview').replaceChildren(element('p','Подбор служб…'));
 try{const result=await api('student/routing/preview','POST',card);if(request!==routingRequest||current.id!==sid||JSON.stringify(readCard())!==fingerprint)return;routingView=result;routingFingerprint=fingerprint;const added=result.suggestions.map(s=>s.service).filter(s=>!current.card.services.includes(s));if(added.length){current.card.services=[...new Set([...current.card.services,...added])];if(current.card.services.length>100)throw Error('В карточке допускается не более 100 служб');dirty=true;for(const id of ['processed','openForward','openBriefing','openNotification','openLinks','printCard'])$(id).disabled=true;$('saveState').textContent='Не сохранено';$('cardPanel').classList.add('dirty');renderServices();}if($('serviceDialog').open)renderServiceChoices();renderServices();renderRouting(result);$('applyRouting').disabled=!result.suggestions.length;}
 catch(e){if(request===routingRequest){$('routingPreview').replaceChildren(element('p',e.message,'routing-warning'));}throw e;}
}
function openServices(){
 const readonly=viewing||current.status==='Завершена'||current.exercise_mode==='actions';routingRequest++;routingView=null;$('applyRouting').disabled=true;
 $('routingFlags').disabled=readonly;$('refreshRouting').hidden=readonly;$('applyRouting').hidden=readonly;
 const card=readCard();$('routingContext').textContent=`Пострадавшие: ${card.injured?'да':'нет'}. Нет доступа: ${card.no_access?'да':'нет'}. Эти признаки меняются в верхней части карточки. Отказ от скорой не считается признаком «не на месте».`;
 renderServiceChoices();$('serviceDialog').showModal();if(readonly)renderRouting(current.routing);else guarded(refreshRouting);
}
$('serviceSearch').oninput=()=>renderServiceChoices();
$('addService').onclick=openServices;
const routingInfo=element('button','основания');routingInfo.type='button';routingInfo.id='routingInfo';routingInfo.onclick=openServices;$('addService').after(routingInfo);
$('refreshRouting').onclick=()=>guarded(refreshRouting);
$('applyRouting').onclick=()=>{
 if(!routingView||routingFingerprint!==JSON.stringify(readCard())||viewing||current.status==='Завершена'||current.exercise_mode==='actions')return;
 const services=[...new Set([...current.card.services,...routingView.suggestions.map(s=>s.service)])];
 if(services.length>100){notify('В карточке допускается не более 100 служб',true);return;}
 current.card.services=services;markDirty();renderServices();renderServiceChoices();notify('Службы добавлены. Сохраните карточку.');
};
$('addResponse').onclick=()=>guarded(async()=>{if(dirty)throw Error('Сначала сохраните карточку');const service=$('responseService').value,status=$('responseStatus').value,order_number=$('responseOrderNumber').value.trim(),comment=$('responseComment').value.trim(),signature=JSON.stringify({sid:current.id,service,status,order_number,comment});if(!service||!status)throw Error('Для этой службы нет доступных статусов');if(!pendingServiceAction||pendingServiceAction.signature!==signature)pendingServiceAction={signature,body:{message_id:crypto.randomUUID(),service,status,order_number,comment}};const button=$('addResponse');button.disabled=true;try{current=await api(`student/sessions/${current.id}/services`,'POST',pendingServiceAction.body);pendingServiceAction=null;$('responseOrderNumber').value='';$('responseComment').value='';renderCard();notify('Статус службы сохранён');}finally{populateResponseStatuses();}});
function renderNotificationHistory(){$('notificationHistory').replaceChildren();for(const item of current.notifications||[]){const tr=element('tr');for(const value of [formatted(item.at),item.service,item.destination,item.phone,item.recipient,item.comment||'—',item.operator||item.operator_name||'Оператор'])tr.append(element('td',value));$('notificationHistory').append(tr);}}
function renderLinkedCards(){$('linkedCards').replaceChildren();if(!(current.linked_cards||[]).length)return;$('linkedCards').append(element('strong','Связанные карточки: '));for(const item of current.linked_cards){const button=element('button',`№ ${item.number}`);button.type='button';button.onclick=()=>guarded(async()=>{if(!confirmLeave())return;$('linksDialog').close();await openSession(item.id);});$('linkedCards').append(button);}}
const ERROR_FIELDS={street:'Улица',house:'Дом',building:'Корпус',structure:'Строение',apartment:'Квартира',entrance:'Подъезд',city:'Город',district:'Район',area:'Округ',object:'Объект',address_note:'Описательный адрес',description:'Описание',incident_type:'Тип происшествия'};
function renderErrorReports(){$('errorReportHistory').replaceChildren();for(const item of current?.error_reports||[]){const tr=element('tr');for(const value of [formatted(item.at),ERROR_FIELDS[item.field]||item.field,item.card_value||'—',item.correct_value,item.recipient])tr.append(element('td',value));$('errorReportHistory').append(tr);}}
$('openErrorReport').onclick=()=>{if(!current?.revision||current.status==='Завершена')return;const select=$('errorReportField');select.replaceChildren();for(const [key,label] of Object.entries(ERROR_FIELDS))select.add(new Option(`${label}: ${current.card[key]||'—'}`,key));renderErrorReports();$('errorReportDialog').showModal();};
let pendingErrorReport=null;
$('errorReportForm').onsubmit=event=>{event.preventDefault();guarded(async()=>{const body={field:$('errorReportField').value,correct_value:$('errorReportValue').value.trim(),source:$('errorReportSource').value.trim(),recipient:$('errorReportRecipient').value.trim(),comment:$('errorReportComment').value.trim()},signature=JSON.stringify({sid:current.id,...body});if(!pendingErrorReport||pendingErrorReport.signature!==signature)pendingErrorReport={signature,body:{message_id:crypto.randomUUID(),...body}};const button=$('saveErrorReport');button.disabled=true;try{current=await api(`student/sessions/${current.id}/error-reports`,'POST',pendingErrorReport.body);pendingErrorReport=null;for(const id of ['errorReportValue','errorReportComment'])$(id).value='';renderErrorReports();renderCard();notify('Сообщение об ошибке передано в Службу 112.');}finally{button.disabled=false;}});};
$('openNotification').onclick=()=>{if(dirty){notify('Сначала сохраните карточку',true);return;}if(!current?.revision||current.status==='Завершена')return;$('notificationService').replaceChildren();for(const service of current.card.services)if(current.exercise_mode!=='actions'||current.card.service_phones?.[service])$('notificationService').add(new Option(service,service));if(!$('notificationService').options.length){notify('У получателей нет доступных телефонов',true);return;}$('notificationDialog').showModal();};
$('notificationForm').onsubmit=event=>{event.preventDefault();guarded(async()=>{if(dirty)throw Error('Сначала сохраните карточку');const body={service:$('notificationService').value,destination:$('notificationDestination').value.trim(),phone:$('notificationPhone').value.trim(),recipient:$('notificationRecipient').value.trim(),comment:$('notificationComment').value.trim()},signature=JSON.stringify({sid:current.id,...body});if(!pendingNotification||pendingNotification.signature!==signature)pendingNotification={signature,body:{message_id:crypto.randomUUID(),...body}};const button=$('saveNotification');button.disabled=true;try{current=await api(`student/sessions/${current.id}/notifications`,'POST',pendingNotification.body);pendingNotification=null;for(const id of ['notificationDestination','notificationPhone','notificationRecipient','notificationComment'])$(id).value='';$('notificationDialog').close();renderCard();notify('Телефонограмма записана.');}finally{button.disabled=false;}});};
$('openLinks').onclick=()=>{if(dirty){notify('Сначала сохраните карточку',true);return;}if(!current?.revision||current.status==='Завершена')return;$('linkTarget').replaceChildren();const linked=new Set((current.linked_cards||[]).map(v=>v.id));for(const item of sessions)if(item.id!==current.id&&!linked.has(item.id))$('linkTarget').add(new Option(`№ ${item.number} · ${item.card.incident_type||'без типа'} · ${addressText(item.card)}`,item.id));$('addLink').disabled=!$('linkTarget').options.length;$('linksDialog').showModal();};
$('addLink').onclick=()=>guarded(async()=>{if(dirty)throw Error('Сначала сохраните карточку');const target_id=$('linkTarget').value;if(!target_id)throw Error('Нет доступной карточки для связи');const button=$('addLink');button.disabled=true;try{current=await api(`student/sessions/${current.id}/links`,'POST',{target_id});$('linksDialog').close();renderCard();notify('Ссылка на карточку сохранена');}finally{button.disabled=false;}});
$('printCard').onclick=()=>{if(dirty){notify('Печатается только сохранённая карточка',true);return;}if(current?.revision)window.print();};
function showAudit() {
 if(!current){notify('Сначала откройте карточку');return;}$('auditContent').replaceChildren();
 for(const note of current.teacher_feedback||[]){const item=element('article');item.append(element('strong',`Преподаватель · ${note.teacher_name} · ${formatted(note.at)}`),element('p',note.text));$('auditContent').append(item);}
 if(current.completed_by?.role==='teacher')$('auditContent').append(element('p',`Завершил преподаватель ${current.completed_by.name}: ${current.completed_by.reason}`));
 if(current.unsaved_draft)$('auditContent').append(element('p','Несохранённый черновик не включён в оценку. Он хранится в этой вкладке и доступен в JSON отдельным полем unsaved_draft.'));
 const review=current.ai_review,finished=current.status==='Завершена';
 if(current.exercise_mode==='actions')$('auditContent').append(element('p','Выдана готовая карточка. Действия и изменения фиксируются отдельно.'));
 renderDdsReview();
 if(current.action_report)$('auditContent').append(element('p',`Изменено полей: ${current.action_report.changed_fields.length}. Действий служб: ${current.action_report.service_actions}. ${current.action_report.note}`));
 if(finished&&current.lesson_id){const next=element('button','Следующая карточка серии');next.onclick=()=>guarded(async()=>{next.disabled=true;try{const result=await api(`student/lessons/${current.lesson_id}/next`,'POST',{...nextMode(),workstation:workstation()});current=result.exercise_mode==='actions'?await api(`student/sessions/${result.id}/open`,'POST',{}):result;pendingLessonSessionId='';dirty=false;viewing=false;transcriptKey='';localStorage.setItem('studentSession',current.id);$('auditDialog').close();renderCard();if(current.exercise_mode!=='actions')$('dialogueDialog').showModal();else notify('Готовая карточка: внесите действия и результаты реагирования служб');}finally{next.disabled=false;}});$('auditContent').append(next);}
 $('requestReview').hidden=!finished;
 $('requestReview').disabled=pendingReviews.has(current.id)||['ready','mock'].includes(review?.status);
 $('requestReview').textContent=pendingReviews.has(current.id)?'ИИ анализирует…':review?.status==='failed'?'Повторить ИИ-разбор':['ready','mock'].includes(review?.status)?'ИИ-разбор сохранён':'Получить ИИ-разбор';
 if(current.status==='Завершена'){
  const report=current.evaluation;
  $('auditContent').append(element('h3','Результат занятия'));
  if(report?.status==='evaluated'){
   $('auditContent').append(element('p',`${report.score_percent}% · ${report.earned_weight} из ${report.total_weight} баллов по эталону «${report.rubric_title}», версия ${report.rubric_revision}.`));
   const timing=report.timing;$('auditContent').append(element('p',`Время занятия: ${timing.elapsed_seconds} сек. Лимит задания: ${timing.limit_seconds} сек. ${timing.within_limit?'В пределах лимита.':'Лимит превышен.'} Время не включено в балл за поля.`));
   for(const criterion of report.criteria){const item=element('article');item.append(element('strong',`${criterion.passed?'✓':'Ошибка'} · ${criterion.label} (${criterion.passed?criterion.weight:0}/${criterion.weight})`));item.append(element('p','Ваш ответ: '+JSON.stringify(criterion.actual)));item.append(element('p','Эталон: '+criterion.expected.join(' / ')));if(!criterion.passed)item.append(element('p',criterion.recommendation));$('auditContent').append(item);}
  }else if(current.dds_review)$('auditContent').append(element('p',`Итог по действиям ДДС: ${current.dds_review.score_percent}% · ${current.dds_review.passed?'зачтено':'не зачтено'}.`));
  else $('auditContent').append(element('p',report?'Оценка не выставлена: при старте занятия эталон не был настроен.':'Это занятие завершено до появления оценивания; пересчёт не выполнялся.'));
  $('auditContent').append(element('h3','Заполненность карточки'));
  const labels={caller_name:'Имя заявителя',street:'Улица',house:'Дом',description:'Описание',incident_type:'Тип происшествия',services:'Службы'};for(const check of current.checks||[])$('auditContent').append(element('p',`${check.passed?'✓':'○'} ${labels[check.field]}: ${check.passed?'заполнено':'не заполнено'}`));
 }else $('auditContent').append(element('p',current.exercise_mode==='actions'&&current.dds_assessment_enabled?'Оценка действий ДДС будет рассчитана после отработки происшествия.':current.assessment_enabled?`Эталон зафиксирован. Лимит занятия: ${current.time_limit_seconds} сек. Правильные ответы откроются после завершения.`:'Для этого занятия эталон не настроен; итоговый балл не будет выставлен.'));
 if(finished){
  $('auditContent').append(element('h3','ИИ-разбор текста · отдельно от балла'));
  if(!review)$('auditContent').append(element('p','Разбор ещё не запрашивался. Запустите его кнопкой ниже.'));
  else if(review.status==='failed')$('auditContent').append(element('p',review.error));
  else{
   $('auditContent').append(element('p',`${review.provider} / ${review.model} · ${formatted(review.created_at)}`,'subtle'),element('p',review.summary));
   const kinds={grammar:'Грамматика',clarity:'Ясность формулировки',contradiction:'Возможное противоречие'},names={description:'Описание',address_note:'Описательный адрес',caller_name:'Имя заявителя',street:'Улица',house:'Дом',apartment:'Квартира',incident_type:'Тип происшествия'};
   for(const finding of review.findings){const block=element('article');block.append(element('strong',`${kinds[finding.kind]||finding.kind} · ${names[finding.field]||finding.field}`),element('p',`В карточке: «${finding.quote}»`),element('p',finding.explanation),element('p',finding.suggestion));for(const ref of finding.references||[])block.append(element('p',`Основание (${ref.id}): ${ref.text}`,'subtle'));$('auditContent').append(block);}
   
  }
 }
 $('auditContent').append(element('h3','Действия в занятии'));
 const types={'session.created':'Создано занятие','card.saved':'Сохранена карточка','card.processed':'Происшествие отработано','card.linked':'Добавлена связь карточки','notification.recorded':'Записана телефонограмма','service.updated':'Статус службы','call.requested':'Запрошен звонок','session.finished':'Завершено занятие','review.completed':'ИИ-разбор сохранён','review.failed':'ИИ-разбор недоступен'};
 for(const event of current.events){const el=element('article');el.append(element('strong',`${formatted(event.at)} · ${types[event.type]||event.type}`));if(Object.keys(event.detail).length)el.append(element('pre',JSON.stringify(event.detail,null,2)));$('auditContent').append(el);}if(!$('auditDialog').open)$('auditDialog').showModal();
}
$('requestReview').onclick=()=>guarded(async()=>{
 if(!current||current.status!=='Завершена'||pendingReviews.has(current.id))return;
 const sid=current.id;pendingReviews.add(sid);showAudit();
 try{const result=await api(`student/sessions/${sid}/ai-review`,'POST');if(current?.id===sid)current=result;}
 finally{pendingReviews.delete(sid);if(current?.id===sid&&$('auditDialog').open)showAudit();}
});
$('export').onclick=()=>{const blob=new Blob([JSON.stringify(current,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=element('a');a.href=url;a.download=`training-${current.number}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('sipCall').onclick=startSipCall;
$('clearAddress').onclick=()=>{for(const f of fields)if(address.some(a=>a[0]===f.dataset.field)||f.dataset.field==='address_note')f.value='';markDirty();};
for(const id of ['create','emptyCreate'])$(id).onclick=()=>guarded(newSession);
for(const id of ['journal','closeCard'])$(id).onclick=()=>{journal();releasePresenceAfterCard();};
$('openCard').onclick=()=>guarded(async()=>{const id=current?.id||localStorage.getItem('studentSession');if(id)await openSession(id);else await newSession();});
for(const id of ['conversation','showDialogue','showText'])$(id).onclick=()=>{if(!current){notify('Сначала откройте или создайте карточку');return;}$('dialogueDialog').showModal();renderDialogue();};
for(const id of ['audit','history'])$(id).onclick=showAudit;
$('help').onclick=()=>$('helpDialog').showModal();

// Статус оператора в телефонии. В инструкции их четыре: доступен, недоступен,
// не подключен, ошибка. Доступен/недоступен переключает сам оператор; при
// открытой карточке система ставит «недоступен» автоматически и возвращает
// «доступен» через 10 секунд после её закрытия.
let presenceManual='available', presenceReturnTimer=null;
function presenceState(){
 if(!navigator.onLine||connectionLost)return 'offline';
 if(healthText.startsWith('○'))return 'error';
 if(current&&!$('cardPanel').hidden&&current.status!=='Завершена')return 'busy';
 return presenceManual==='busy'?'busy':'available';
}
const PRESENCE_TEXT={available:'доступен',busy:'недоступен',offline:'не подключен',error:'ошибка'};
function renderPresence(){
 const state=presenceState();
 $('phonePresenceText').textContent=PRESENCE_TEXT[state];
 $('phonePresence').dataset.state=state;
 $('phonePresence').disabled=state==='offline'||state==='error';
}
$('phonePresence').onclick=()=>{
 const state=presenceState();
 if(state==='offline'||state==='error')return;
 if(current&&!$('cardPanel').hidden&&current.status!=='Завершена'){
  notify('При открытой карточке статус «недоступен» ставится автоматически');return;
 }
 presenceManual=presenceManual==='busy'?'available':'busy';renderPresence();
};
function releasePresenceAfterCard(){
 // Десять секунд после закрытия карточки — как в инструкции.
 clearTimeout(presenceReturnTimer);
 renderPresence();
 presenceReturnTimer=setTimeout(()=>{presenceManual='available';renderPresence();},10000);
}
setInterval(renderPresence,2000);


// Напоминание («будильник») по карточке. В инструкции оно всплывает по
// наступлении срока, и, если оператор его закрыл без действия, повторяется.
// Здесь окно повторяется раз в 20 секунд, как в источнике.
const firedReminders = new Set();
function renderReminders(){
 const list=$('reminderList');list.replaceChildren();
 const items=current?.reminders||[];
 if(!items.length){list.append(element('p','Напоминаний по этой карточке нет.','subtle'));return;}
 for(const item of items){
  const at=new Date(item.at);
  list.append(element('p',`${at.toLocaleString('ru-RU')} — ${item.text}`));
 }
}
$('openReminder').onclick=()=>{
 if(!current)return;
 $('reminderText').value='';$('reminderMinutes').value='5';renderReminders();$('reminderDialog').showModal();
};
$('reminderForm').onsubmit=event=>{event.preventDefault();guarded(async()=>{
 const minutes=Number($('reminderMinutes').value);
 if(!Number.isFinite(minutes)||minutes<1)throw Error('Укажите время в минутах');
 const at=new Date(Date.now()+minutes*60000).toISOString();
 current=await api(`student/sessions/${current.id}/reminders`,'POST',{message_id:crypto.randomUUID(),text:$('reminderText').value.trim(),at});
 renderReminders();$('reminderText').value='';notify('Напоминание поставлено');
});};
// По инструкции окно напоминания появляется именно при закрытой карточке —
// оператор её отложил и должен вернуться. Поэтому срок проверяется по всем
// своим карточкам журнала, а не только по открытой, и пока оператор не
// отреагировал, окно повторяется каждые 20 секунд.
const repeatingReminders=new Map();
function dueReminders(){
 const at=new Date(),result=[];
 const pool=[...sessions,...(current&&!sessions.some(s=>s.id===current.id)?[current]:[])];
 for(const session of pool){
  if(session.status==='Завершена')continue;
  for(const item of session.reminders||[]){
   if(new Date(item.at)<=at)result.push({session,item});
  }
 }
 return result;
}
function showReminder(session,item){
 $('reminderAlertText').textContent=item.text;
 $('reminderAlertCard').textContent=`Карточка № ${session.number} · ${session.card?.incident_type||'без типа'}`;
 $('reminderAlert').dataset.sessionId=session.id;
 $('reminderAlert').dataset.messageId=item.message_id;
 if(!$('reminderAlert').open&&!document.querySelector('dialog[open]'))$('reminderAlert').showModal();
 else notify(`Напоминание: ${item.text}`);
}
function checkReminders(){
 for(const {session,item} of dueReminders()){
  const key=item.message_id;
  if(firedReminders.has(key))continue;
  firedReminders.add(key);
  showReminder(session,item);
  const repeat=setInterval(()=>{
   if(dismissedReminders.has(key)){clearInterval(repeat);repeatingReminders.delete(key);return;}
   showReminder(session,item);
  },20000);
  repeatingReminders.set(key,repeat);
 }
}
const dismissedReminders=new Set();
function dismissReminder(key){
 dismissedReminders.add(key);
 const timer=repeatingReminders.get(key);
 if(timer){clearInterval(timer);repeatingReminders.delete(key);}
 $('reminderAlert').close();
}
$('openReminderCard').onclick=()=>{
 const {sessionId,messageId}=$('reminderAlert').dataset;
 dismissReminder(messageId);
 guarded(()=>openSession(sessionId));
};
$('snoozeReminder').onclick=()=>{
 // Отложить — напоминание остаётся в карточке, но перестаёт всплывать.
 dismissReminder($('reminderAlert').dataset.messageId);
 notify('Напоминание отложено; оно останется в карточке');
};
setInterval(checkReminders,5000);

// Нерезультативный вызов: нет контакта или срыв звонка. По инструкции такая
// карточка сохраняется пустой, уходит в завершённый статус с отметкой о
// проверке, и оператору предлагается либо это подтвердить, либо вернуться к
// заполнению — на случай, если кнопка нажата по ошибке.
let unproductiveKind='';
const UNPRODUCTIVE_NAMES={no_contact:'Нет контакта',interrupted:'Срыв звонка'};
function askUnproductive(kind){
 unproductiveKind=kind;
 $('unproductiveTitle').textContent=UNPRODUCTIVE_NAMES[kind];
 $('unproductiveText').textContent=`Сохранить карточку как пустую? Она будет завершена с отметкой «${UNPRODUCTIVE_NAMES[kind]}» и проверена. Заполнять её больше нельзя.`;
 $('unproductiveDialog').showModal();
}
for(const kind of ['no_contact','interrupted']){
 const input=document.querySelector(`[data-field="${kind}"]`);
 if(!input)continue;
 input.addEventListener('change',event=>{
  if(!input.checked||!current||current.status==='Завершена'||viewing)return;
  if(current.card.services?.length){notify('В карточке уже назначены службы: вызов не считается нерезультативным',true);return;}
  event.preventDefault();askUnproductive(kind);
 });
}
$('confirmUnproductive').onclick=()=>guarded(async()=>{
 const button=$('confirmUnproductive');button.disabled=true;
 try{
  current=await api(`student/sessions/${current.id}/unproductive`,'POST',{message_id:crypto.randomUUID(),kind:unproductiveKind});
  dirty=false;viewing=true;clearDraft(current.id);$('unproductiveDialog').close();renderCard();
  notify('Карточка закрыта как нерезультативный вызов');
 }finally{button.disabled=false;}
});
$('unproductiveDialog').addEventListener('close',()=>{
 // Отказ от подтверждения снимает флажок: карточка остаётся в работе.
 const input=document.querySelector(`[data-field="${unproductiveKind}"]`);
 if(input&&current&&current.status!=='Завершена')input.checked=!!current.card[unproductiveKind];
});


// Горячие клавиши рабочего места. Набор и назначения взяты из инструкции
// оператора Системы 112: Insert — новая карточка, Esc — закрыть, Alt+буква —
// переход к блоку карточки, а удержание Alt показывает подсказки прямо на
// экране. Для тренажёра это не украшение: диспетчер работает клавишами, и
// привычка к ним и есть часть навыка.
const HOTKEYS = [
 {key:'t', hint:'Что случилось', target:()=>$('incidentType')},
 {key:'a', hint:'Адрес', target:()=>$('addressFields').querySelector('input')},
 {key:'q', hint:'Заявитель', target:()=>document.querySelector('[data-field="caller_name"]')},
 {key:'f1', hint:'АОН', target:()=>document.querySelector('[data-field="phone"]')},
 {key:'f2', hint:'Предоставленный', target:()=>document.querySelector('[data-field="supplied_phone"]')},
 {key:'f3', hint:'Телефон на место', target:()=>document.querySelector('[data-field="scene_phone"]')},
 {key:'p', hint:'Пострадавшие', target:()=>document.querySelector('[data-field="injured"]')},
 {key:'n', hint:'Нет контакта / срыв', target:()=>document.querySelector('[data-field="no_contact"]')},
 {key:'o', hint:'Описание', target:()=>$('description')},
 {key:'z', hint:'Службы', action:()=>{if(current&&!$('addService').hidden)$('addService').click();}},
 {key:'s', hint:'Сохранить', action:()=>{if(current&&!$('save').hidden)$('save').click();}},
 {key:'w', hint:'Связать карточки', action:()=>{if(current&&!$('openLinks').disabled)$('openLinks').click();}},
 {key:'b', hint:'Напоминание', action:()=>{if(current&&!$('openReminder').disabled)$('openReminder').click();}},
 {key:'v', hint:'Важное происшествие', action:()=>{
   const flag=document.querySelector('[data-field="important"]');
   if(!flag||flag.disabled)return;flag.checked=!flag.checked;flag.dispatchEvent(new Event('input',{bubbles:true}));
   notify(flag.checked?'Происшествие помечено важным':'Отметка важности снята');}},
 {key:'m', hint:'Справка', action:()=>$('helpDialog').showModal()},
];
function hotkeyHintsVisible(show){
 document.body.classList.toggle('alt-hints',show);
 if(!show){for(const node of document.querySelectorAll('.hotkey-hint'))node.remove();return;}
 if(document.querySelector('.hotkey-hint'))return;
 for(const item of HOTKEYS){
  const node=item.action?null:item.target?.();
  const host=node?.closest('label')||node;
  if(!host||host.offsetParent===null)continue;
  const hint=element('span',item.key.startsWith('f')?`Alt+${item.key.toUpperCase()}`:`Alt+${item.key.toUpperCase()}`,'hotkey-hint');
  host.style.position=host.style.position||'relative';host.append(hint);
 }
}
function focusTarget(node){
 if(!node||node.disabled)return false;
 node.focus({preventScroll:false});
 if(typeof node.scrollIntoView==='function')node.scrollIntoView({block:'nearest'});
 return true;
}
document.addEventListener('keydown',event=>{
 if(event.key==='Alt'&&!event.repeat&&!$('cardPanel').hidden){hotkeyHintsVisible(true);return;}
 const editing=/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName||'');
 if(event.key==='Insert'&&!event.altKey&&!event.ctrlKey){event.preventDefault();guarded(newSession);return;}
 if(event.key==='Escape'&&!document.querySelector('dialog[open]')&&!$('cardPanel').hidden){
  if($('cardPanel').classList.contains('response-open')){event.preventDefault();$('cardPanel').classList.remove('response-open');return;}
  // Esc закрывает карточку только когда не открыт ни один диалог: иначе
  // браузер сам закрывает верхний диалог, и это ожидаемое поведение.
  if(dirty){notify('Есть несохранённые изменения. Сохраните карточку или восстановите черновик позже.',true);return;}
  event.preventDefault();journal();return;
 }
 if(!event.altKey||event.ctrlKey||event.metaKey)return;
 const pressed=(event.key||'').toLowerCase();
 const item=HOTKEYS.find(h=>h.key===pressed);
 if(!item)return;
 if($('cardPanel').hidden)return;
 event.preventDefault();
 hotkeyHintsVisible(false);
 if(item.action){item.action();return;}
 if(!focusTarget(item.target()))notify('Этот блок сейчас недоступен');
});
document.addEventListener('keyup',event=>{if(event.key==='Alt')hotkeyHintsVisible(false);});
window.addEventListener('blur',()=>hotkeyHintsVisible(false));

const materialsHelp=document.createElement('p'),materialsLink=document.createElement('a');
materialsLink.href='/materials';materialsLink.target='_blank';materialsLink.rel='noopener';materialsLink.textContent='Справочные материалы моей группы (в новой вкладке)';
materialsHelp.append(materialsLink);$('helpDialog').append(materialsHelp);
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
$('stopSpeech').onclick=()=>window.speechSynthesis?.cancel();
$('speak').onchange=()=>{if(!$('speak').checked)window.speechSynthesis?.cancel();else if(current?.messages.at(-1)?.role==='assistant')say(current.messages.at(-1).content);};
$('refresh').onclick=()=>guarded(loadSessions);$('search').oninput=renderRows;$('statusFilter').onchange=renderRows;$('lessonScope').onchange=renderRows;$('filters').onclick=()=>{const hidden=$('advancedFilters').hidden=!$('advancedFilters').hidden;$('filters').textContent=hidden?'расширенный по параметрам⌄':'скрыть фильтры⌃';$('filters').setAttribute('aria-expanded',String(!hidden));};
const advancedInputs=[...$('advancedFilters').querySelectorAll('input,select')];for(const input of advancedInputs)input.addEventListener('input',renderRows);
function resetFilters(all=false){for(const input of advancedInputs)input.value='';if(all){$('search').value='';$('statusFilter').value='';}renderRows();}
$('applyAdvanced').onclick=renderRows;$('resetAdvanced').onclick=()=>resetFilters(false);$('resetSearch').onclick=()=>resetFilters(true);
// В реальной Системе 112 поле таймера краснеет, когда время набора карточки
// превышено. В ДДС 30 секунд идут от поступления до открытия карточки, 3 минуты —
// до первой записи (статус и текст); дальше работы не нормируются.
function tick(){
 const date=new Date();
 $('today').textContent=date.toLocaleDateString('ru-RU',{weekday:'long',day:'numeric',month:'long',year:'numeric'});
 $('clock').textContent=date.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
 if(!current)return;
 const seconds=Math.max(0,Math.floor(((current.finished_at?new Date(current.finished_at):date)-new Date(current.created_at))/1000));
 $('timer').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;
 const dds=current.exercise_mode==='actions'&&current.owner_service;
 const stage=!dds?null:!current.opened_at?'open':!current.first_record_at?'record':'free';
 const limit=dds?{open:current.time_limit_seconds||30,record:180,free:null}[stage]:current.time_limit_seconds;
 const since=stamp=>Math.max(0,Math.floor((new Date(stamp)-new Date(current.created_at))/1000));
 // Норматив, уже нарушенный на прошлом этапе, остаётся красным до конца карточки.
 const missed=dds&&((current.opened_at&&since(current.opened_at)>(current.time_limit_seconds||30))
  ||(current.first_record_at&&since(current.first_record_at)>180));
 const over=missed||(Number.isFinite(limit)&&limit>0&&seconds>limit);
 $('cardPanel').querySelector('.timer').classList.toggle('overdue',over);
 const label=dds?{open:'открытия карточки',record:'первой записи (статус и текст)',free:'—'}[stage]:'занятия';
 if(stage==='free'){$('timer').title=missed?'Норматив открытия или первой записи был превышен. Дальнейшие работы не нормируются.':'Нормативы выполнены. Дальнейшие работы не нормируются.';return;}
 $('timer').title=(limit&&seconds>limit)?`Норматив ${label} ${limit} с превышен`:limit?`Норматив ${label} ${limit} с${missed?' (норматив открытия превышен)':''}`:'Норматив не задан';
}
async function recoverCurrentSession(){
 if(recoveryBusy||!navigator.onLine)return;recoveryBusy=true;
 const sid=current?.id,wasDirty=!!(current&&dirty),draft=wasDirty?readCard():null;
 if(draft)persistDraft(draft);
 try{
  if(sid){
   const data=await api(`student/sessions/${sid}`);if(current?.id!==sid)return;
   attemptedLessonCalls.add(sid);
   if(current.status!=='Завершена'&&data.status==='Завершена'){receiveRemoteCompletion(data);return;}
   const savedCard=data.card;current=data;attachStoredDraft(current);
   if(wasDirty){savedServices=new Set(savedCard.services||[]);current.card=draft;current.unsaved_draft=draft;dirty=true;viewing=false;renderCard();markDirty();}
   else{dirty=false;viewing=current.revision>0;renderCard();}
  }
  healthText='● Связь с Backend восстановлена';renderConnection();notify(wasDirty?'Связь восстановлена. Черновик сохранён и не заменён данными сервера.':'Связь восстановлена. Карточка обновлена с сервера.');
 }finally{recoveryBusy=false;}
}
async function refreshHealth(){try{const h=await api('health');healthText=h.status==='ok'?(h.provider==='mock'?'● Mock-режим':'● Сервис диалога подключён'):'○ Сервис диалога не настроен';renderConnection();}catch{renderConnection();}}
window.addEventListener('offline',()=>{noteDisconnected();notify('Соединение потеряно. Несохранённая карточка остаётся в этой вкладке.',true);});
window.addEventListener('online',()=>{healthText='Проверка связи…';$('health').textContent=healthText;guarded(recoverCurrentSession);});
window.addEventListener('beforeunload',e=>{if(dirty){persistDraft();e.preventDefault();e.returnValue='';}});
let feedbackPolling=false;
const backgroundUpdateKeys=new Set();
setInterval(async()=>{
 if(feedbackPolling||!studentUserId)return;
 feedbackPolling=true;
 try{
  const values=await api('student/inbox/poll','POST',{});
  for(const value of values){
   if(value.id===current?.id)continue;
   for(const item of [...(value.situation_updates||[]),...(value.pending_phone_reports||[])]){
    const key=value.id+':'+item.id;
    if(backgroundUpdateKeys.has(key))continue;
    backgroundUpdateKeys.add(key);notify(`Карточка № ${value.number}: новое сообщение от ${item.source}`);
   }
  }
  if(!current)return;
  const sid=current.id;
  const data=values.find(value=>value.id===sid)||await api(`student/sessions/${sid}`);
  if(current?.id!==sid)return;
  if(current.status!=='Завершена'&&data.status==='Завершена'){receiveRemoteCompletion(data);return;}
  current.incident_status=data.incident_status;current.completion_missing=data.completion_missing;updateIncidentState();receiveSituationUpdates(data);window.updateDdsCoach?.(current);
  const previous=(current.teacher_feedback||[]).length;current.teacher_feedback=data.teacher_feedback||[];
  if(current.teacher_feedback.length>previous){notify('Новое замечание преподавателя — откройте «Отчёт и история»');if($('auditDialog').open)showAudit();}
 }catch{/* Connection state is displayed by the common health indicator. */}
 finally{feedbackPolling=false;}
},5000);
let polling=false;setInterval(async()=>{if(polling)return;polling=true;try{if($('autoRefresh').checked&&!$('journalPanel').hidden)await loadSessions();if(current?.transport==='sip'&&current.call_id&&current.status!=='Завершена'){
 const sid=current.id,data=await api(`student/sessions/${sid}`);if(current?.id!==sid)return;
 current.messages=data.messages;current.provider_error=data.provider_error;current.call_id=data.call_id;renderDialogue();
 if(data.status==='Завершена'){current.status=data.status;renderCard();return;}
 const call=await api(`student/sessions/${sid}/call`);$('callStatus').textContent=({active:'звонок активен',calling:'вызов…',ringing:'примите звонок',ended:'звонок завершён',failed:'ошибка звонка'})[call.status]||call.status;
 if(call.recovery_allowed&&call.recovery?.state!=='exhausted'){
  $('callStatus').textContent='восстанавливаем соединение…';
  try{const restored=await api(`student/sessions/${sid}/call/recover?expected_call_id=${encodeURIComponent(call.call_id)}`,'POST');if(current?.id===sid){current.call_id=restored.call_id;$('callStatus').textContent='повторный вызов — примите звонок';}}
  catch(error){$('callStatus').textContent='Восстановление: '+error.message;}
 }
 }}catch{if(current?.transport==='sip')$('callStatus').textContent='нет связи с Voice';}finally{polling=false;}},3000);
setInterval(tick,1000);tick();guarded(async()=>{const user=await api('auth/me');if(user.role!=='student'){location.replace('/portal');return;}studentUserId=user.id;$('operatorSeat').textContent=`${user.display_name} · АРМ ${workstation()||'не указан'}`;activeLessonId=sessionStorage.getItem('activeLesson:'+studentUserId)||'';await loadClassifier();await loadSessions();await refreshLessonFlow();refreshHealth();setInterval(refreshHealth,15000);});

// Доклад дежурному службы: учебный вызов из ДДС в службу (направление Б→C).
let briefing=null,briefingTimer=null;
// Режим следующей карточки: полный цикл 112 или работа диспетчера ДДС.
// Переключение разрешает преподаватель; текущая карточка не меняется.
let studentMode='actions';
// Режим отправляется, только когда кнопка переключения видна.
const nextMode=()=>$('modeSwitch').hidden?{}:{mode:studentMode};
function briefingLine(role,text){const p=element('p',text);p.className=role==='assistant'?'duty':'me';return p;}
function renderBriefing(){
 const box=$('briefingTranscript');box.replaceChildren();
 if(!briefing){$('briefingForm').hidden=true;$('briefingFinishForm').hidden=true;$('briefingMissing').textContent='';return;}
 for(const message of briefing.messages)box.append(briefingLine(message.role,message.content));
 box.scrollTop=box.scrollHeight;
 const open=briefing.state==='open',report=briefing.report||{},voice=briefing.transport==='sip';
 $('briefingForm').hidden=!open||voice;
 $('briefingFinishForm').hidden=!open||!report.complete;
 $('briefingMissing').className='briefing-missing'+(report.complete?' ready':'');
 $('briefingMissing').textContent=!open?'Доклад принят.'
  :briefing.recovery?.state==='exhausted'?'Автовосстановление исчерпано. Проверьте телефон и создайте новый доклад; предыдущие реплики сохранены.'
  :voice&&!briefing.messages.length?'Ожидание ответа по телефону…'
  :report.complete?'Сведения названы полностью. Можно завершать доклад.'
  :'Ещё не названо: '+(report.missing||[]).join(', ');
}
$('openBriefing').onclick=()=>{
 if(dirty){notify('Сначала сохраните карточку',true);return;}
 if(!current?.revision||current.status==='Завершена')return;
 briefing=null;renderBriefing();
 $('briefingService').replaceChildren();
 for(const service of current.card.services)if(current.exercise_mode!=='actions'||current.card.service_phones?.[service])$('briefingService').add(new Option(service,service));
 if(current.assigned_crew?.phone)$('briefingService').add(new Option(`${current.assigned_crew.id} · ${current.assigned_crew.leader}`,'crew:'+current.assigned_crew.id));
 if(current.assigned_crew?.phone)$('briefingService').value='crew:'+current.assigned_crew.id;
 if(!$('briefingService').options.length){notify('У получателей нет доступных телефонов',true);return;}
 $('briefingPhone').value=current.card.service_phones?.[$('briefingService').value]||'';
 $('briefingPhone').readOnly=current.exercise_mode==='actions';
 $('briefingTransportLabel').hidden=!current.sip_extension||current.exercise_mode==='actions';
 $('briefingTransport').value=current.sip_extension?'sip':'text';
 $('briefingText').value='';$('briefingRecipient').value='';
 $('briefingService').onchange();
 $('briefingDialog').showModal();
};
$('briefingService').onchange=()=>{const crew=$('briefingService').value.startsWith('crew:');if(crew){$('briefingPhone').value=current.assigned_crew.phone;$('briefingDestination').value=current.assigned_crew.leader;}else if(current?.exercise_mode==='actions'){$('briefingPhone').value=current.card.service_phones?.[$('briefingService').value]||'';$('briefingDestination').value='';}};
$('startBriefing').onclick=()=>guarded(async()=>{
 if(!$('briefingService').value)throw Error('Сначала сохраните службу в карточке');
 const transport=current.sip_extension?$('briefingTransport').value:'text';
 const crew=$('briefingService').value.startsWith('crew:');
 briefing=await api(`student/sessions/${current.id}/briefings`,'POST',{message_id:crypto.randomUUID(),
  service:crew?current.owner_service:$('briefingService').value,crew_id:crew?current.assigned_crew.id:'',destination:$('briefingDestination').value.trim(),
  phone:$('briefingPhone').value.trim(),transport});
 renderBriefing();
 notify(transport==='sip'?'Вызов создан: говорите по учебному телефону':'Соединение установлено');
 if(transport==='sip')pollBriefing();
});
// В голосовом докладе реплики приходят из разговора, поэтому окно их подтягивает.
function pollBriefing(){
 clearInterval(briefingTimer);
 briefingTimer=setInterval(async()=>{
  if(!briefing||briefing.state!=='open'||!$('briefingDialog').open){clearInterval(briefingTimer);return;}
  try{
   const list=await api(`student/sessions/${current.id}/briefings`);
   const fresh=list.find(item=>item.id===briefing.id);
   if(fresh){briefing=fresh;renderBriefing();
    if(fresh.transport==='sip'&&fresh.call_id&&fresh.recovery?.state!=='exhausted'){
     const sid=current.id,bid=fresh.id;
     const restored=await api(`student/sessions/${sid}/briefings/${bid}/recover?expected_call_id=${encodeURIComponent(fresh.call_id)}`,'POST');
     if(current?.id===sid&&briefing?.id===bid){briefing=restored;renderBriefing();if(restored.call_id!==fresh.call_id)notify('Связь восстанавливается: примите повторный звонок. Доклад сохранён.');}
    }
   }
  }catch{/* окно останется с прежним состоянием до следующей попытки */}
 },2500);
}
$('briefingForm').onsubmit=event=>{event.preventDefault();guarded(async()=>{
 const text=$('briefingText').value.trim();
 if(!briefing||!text)return;
 briefing=await api(`student/sessions/${current.id}/briefings/${briefing.id}/messages`,'POST',
  {message_id:crypto.randomUUID(),text});
 $('briefingText').value='';renderBriefing();
});};
$('briefingFinishForm').onsubmit=event=>{event.preventDefault();guarded(async()=>{
 const recipient=$('briefingRecipient').value.trim();
 if(!briefing||!recipient)return;
 briefing=await api(`student/sessions/${current.id}/briefings/${briefing.id}/finish`,'POST',
  {message_id:crypto.randomUUID(),recipient});
 current=await api(`student/sessions/${current.id}`);
 renderCard();renderBriefing();
 notify('Доклад принят и записан телефонограммой');
});};

// Перенаправление в другую службу: третье действие диспетчера наряду с приёмом
// и отказом. Служба добавляется в карточку, но событие пишется отдельным типом.
$('openForward').onclick=()=>{
 if(current?.exercise_mode==='actions')return;
 if(dirty){notify('Сначала сохраните карточку',true);return;}
 if(!current?.revision||current.status==='Завершена')return;
 const select=$('forwardService');select.replaceChildren();
 for(const service of (serviceNames||[]))
  if(!current.card.services.includes(service))select.add(new Option(service,service));
 if(!select.options.length){notify('Все службы каталога уже есть в карточке',true);return;}
 $('forwardReason').value='';
 $('forwardDialog').showModal();
};
$('forwardForm').onsubmit=event=>{event.preventDefault();guarded(async()=>{
 const service=$('forwardService').value,reason=$('forwardReason').value.trim();
 if(!service||!reason)return;
 current=await api(`student/sessions/${current.id}/forward`,'POST',
  {message_id:crypto.randomUUID(),service,reason});
 renderCard();$('forwardDialog').close();
 notify(`Происшествие перенаправлено: ${service}`);
});};

$('modeSwitch').onclick=()=>guarded(async()=>{
 studentMode=studentMode==='fill'?'actions':'fill';
 $('modeSwitch').textContent=studentMode==='fill'
  ? 'Обычный режим: работа диспетчера ДДС'
  : 'Расширенный режим: полный цикл 112';
 notify(studentMode==='fill'
  ? 'Следующая карточка придёт с приёмом вызова от заявителя'
  : 'Следующая карточка придёт готовой, как в ДДС');
});
