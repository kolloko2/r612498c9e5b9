'use strict';

const el=id=>document.getElementById(id);
const manageableServices=new Set(['voice','asterisk']);
const serviceTitles={backend:'Backend',voice:'Voice',asterisk:'Asterisk',frontend:'Frontend',postgres:'PostgreSQL',directory:'Каталог пользователей', 'backend-lb':'Балансировщик Backend'};
const stateTitles={running:'Работает',started:'Запущена',stopped:'Остановлена',starting:'Запускается',stopping:'Останавливается',restarting:'Перезапускается',healthy:'Исправна',unhealthy:'Неисправна',unavailable:'Недоступна',unknown:'Нет данных',mock:'Имитация',queued:'В очереди',pending:'Ожидает',running_job:'Выполняется',simulated:'Выполнена в режиме имитации',succeeded:'Завершена',success:'Завершена',complete:'Завершена',completed:'Завершена',in_progress:'Выполняется',failed:'Ошибка',error:'Ошибка'};
const metricTitles={cpu:'Процессор',cpu_percent:'Процессор, %',memory:'Память',memory_percent:'Память, %',disk:'Диск',disk_percent:'Диск, %',uptime:'Время работы',uptime_seconds:'Время работы, сек.',requests:'Запросы',active_calls:'Активные звонки',database_size:'Размер базы',used:'Использовано',free:'Свободно',available:'Доступно',total:'Всего',used_bytes:'Использовано',free_bytes:'Свободно',available_bytes:'Доступно',total_bytes:'Всего',size_bytes:'Размер',used_percent:'Использовано, %',source:'Источник',host:'Узел',mode:'Режим',timezone:'Часовой пояс',web_bind_address:'Адрес веб-интерфейса',web_port:'Порт веб-интерфейса',sip_bind_address:'Адрес SIP',sip_external_address:'Внешний адрес SIP',sip_local_net:'Локальная сеть SIP',sip_port:'Порт SIP',rtp_ports:'Порты RTP',allowed_extensions:'Разрешённые внутренние номера',pipeline_mode:'Режим голосового конвейера',topology_verified:'Топология проверена'};
let latestSnapshot=null;
let pollInFlight=false;
let settingsDirty=false;
let savedSettings=null;
let dismissedAlertKeys=new Set();

function node(tag,text,className){const value=document.createElement(tag);if(text!==undefined)value.textContent=String(text);if(className)value.className=className;return value;}
function formatDate(value){if(!value)return 'Время не указано';const numeric=typeof value==='number'?value:Number.NaN,date=new Date(Number.isFinite(numeric)&&Math.abs(numeric)<1e12?numeric*1000:value);return Number.isNaN(date.valueOf())?String(value):date.toLocaleString('ru-RU');}
function formatBytes(value){const amount=Number(value);if(!Number.isFinite(amount)||amount<0)return formatValue(value);const units=['Б','КиБ','МиБ','ГиБ','ТиБ'];let shown=amount,index=0;while(shown>=1024&&index<units.length-1){shown/=1024;index++;}return `${shown.toLocaleString('ru-RU',{maximumFractionDigits:index?2:0})} ${units[index]}`;}
function formatValue(value){
 if(value===null||value===undefined||value==='')return '—';
 if(typeof value==='boolean')return value?'Да':'Нет';
 if(Array.isArray(value))return value.map(formatValue).join(', ')||'—';
 if(typeof value==='object')return Object.entries(value).map(([key,item])=>`${key}: ${formatValue(item)}`).join('; ');
 return String(value);
}
function apiError(data,status){const value=data?.detail?.message||data?.detail||data?.message||data?.error;if(typeof value==='string')return value;if(Array.isArray(value))return value.map(item=>item?.msg||'Некорректный запрос').join('; ');return `Ошибка HTTP ${status}`;}
async function api(path,method='GET',body){
 const options={method,headers:{'X-Voice-UI':'1'}};
 if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options);
 if(response.status===401){location.replace('/login');throw Error('Требуется вход');}
 if(response.status===204)return null;
 const data=await response.json().catch(()=>({}));
 if(!response.ok)throw Error(apiError(data,response.status));
 return data;
}
function normalizedState(value){return String(value||'unknown').trim().toLowerCase();}
function healthGood(value){return value===true||['ok','healthy','ready','up','available','pass','passing'].includes(normalizedState(value));}
function serviceTone(service,stale){
 if(stale)return 'warn';
 const state=normalizedState(service?.state),healthy=healthGood(service?.health);
 if(state==='mock'||normalizedState(service?.health)==='mock')return 'warn';
 return healthy&&['running','started','healthy','ready','up','available'].includes(state)?'good':'bad';
}
function labelForState(value){const key=normalizedState(value);return stateTitles[key]||String(value||'Недоступна');}
function controlsAllowed(){return Boolean(latestSnapshot?.enabled)&&!latestSnapshot?.stale;}
function backupAllowed(){return Boolean(latestSnapshot?.enabled)&&Boolean(latestSnapshot?.backup_available);}
function eventTone(event){const value=normalizedState(event?.severity||event?.level||event?.status||event?.type);if(/critical|fatal|error|failed|failure|unavailable|down/.test(value))return 'bad';if(/warn|stale|degrad/.test(value))return 'warn';return '';}
function eventKey(event,index){return String(event?.id||event?.event_id||`${event?.at||event?.created_at||event?.timestamp||''}:${event?.type||event?.title||index}`);}
function eventTitle(event){return formatValue(event?.title||event?.message||event?.type||event?.event_type||'Техническое событие');}
function eventDetail(event){return formatValue(event?.detail||event?.description||event?.service||event?.action||'');}
function eventTime(event){return formatDate(event?.at||event?.created_at||event?.timestamp);}

function renderSummary(data){
 const status=data.status||{},services=Array.isArray(status.services)?status.services:[],stale=Boolean(data.stale),tones=services.map(item=>serviceTone(item,stale));
 let tone='good',title='Штатно',hint='Все известные службы исправны';
 if(stale){tone='warn';title='Данные устарели';hint='Требуется новый снимок состояния';}
 else if(!services.length||tones.includes('bad')){tone='bad';title='Требует внимания';hint=!services.length?'Службы недоступны для проверки':'Есть неисправные или недоступные службы';}
 else if(tones.includes('warn')){tone='warn';title='Режим имитации';hint='Некоторые службы работают в режиме имитации';}
 const card=el('overallState').closest('.summary-card');card.className=`summary-card ${tone}`;el('overallState').textContent=title;el('overallHint').textContent=hint;
 const sampled=status.sampled_at||data.sampled_at;el('sampledAt').textContent=sampled?formatDate(sampled):'Нет данных';
 el('sampleAge').textContent=stale?'Снимок помечен сервером как устаревший':'Последний серверный снимок';
 el('serviceCount').textContent=services.length?`${tones.filter(item=>item==='good').length} / ${services.length}`:'0 / 0';el('serviceHint').textContent='исправны из известных';
 const jobs=Array.isArray(data.jobs)?data.jobs:[];el('jobCount').textContent=String(jobs.length);
}
function serviceActionButton(service,action,label,danger=false){
 const button=node('button',label,danger?'danger-button':'');button.type='button';button.disabled=!controlsAllowed();
 button.addEventListener('click',async()=>{
  if((action==='stop'||action==='restart')&&!confirm(`${label} службу «${serviceTitles[service]||service}»? Активные учебные звонки могут быть прерваны.`))return;
  await queueJob(action,service,button);
 });return button;
}
function renderServices(data){
 const container=el('services'),services=Array.isArray(data.status?.services)?data.status.services:[];container.replaceChildren();
 if(!services.length){container.append(node('p','Службы недоступны для проверки','empty'));return;}
 for(const service of services){
  const name=normalizedState(service.name),tone=serviceTone(service,Boolean(data.stale)),card=node('article',undefined,`service-card ${tone}`),line=node('p',undefined,'status-line');
  line.append(node('span',undefined,'dot'),node('strong',data.stale?'Состояние устарело':tone==='good'?'Исправна':tone==='warn'?'Имитация':'Недоступна или неисправна'));
  card.append(node('h3',serviceTitles[name]||service.name||'Неизвестная служба'),line,node('p',`Процесс: ${labelForState(service.state)} · проверка: ${formatValue(service.health)}`));
  if(Number.isInteger(service.expected_replicas))card.append(node('p',`Исправных реплик: ${service.healthy_replicas} / ${service.expected_replicas}`));
  if(manageableServices.has(name)){const actions=node('div',undefined,'actions');if(['stopped','exited','dead','down','unavailable','not running'].includes(normalizedState(service.state)))actions.append(serviceActionButton(name,'start','Запустить'));else actions.append(serviceActionButton(name,'restart','Перезапустить'),serviceActionButton(name,'stop','Остановить',true));card.append(actions);}
  container.append(card);
 }
}
function flattenMetrics(value,prefix='',rows=[]){
 if(value&&typeof value==='object'&&!Array.isArray(value)){for(const [key,item] of Object.entries(value))flattenMetrics(item,prefix?`${prefix} · ${key}`:key,rows);}
 else rows.push([prefix||'Значение',formatValue(value)]);
 return rows;
}
function metricLabel(key){return key.split(' · ').map(part=>metricTitles[part]||part.replaceAll('_',' ')).join(' · ');}
function metricValue(key,value){if(/(^|_)(bytes?|size)(_|$)/i.test(key)||/_bytes$/i.test(key))return formatBytes(value);if(/percent$/i.test(key)&&value!=='—')return `${value} %`;return value;}
function renderMetrics(data){
 const container=el('metrics'),rows=flattenMetrics(data.status?.metrics||data.metrics||{});container.replaceChildren();
 if(!rows.length){const row=node('div');row.append(node('dt','Данные'),node('dd','Пока не получены'));container.append(row);return;}
 for(const [key,value] of rows){const row=node('div');row.append(node('dt',metricLabel(key)),node('dd',metricValue(key,value)));container.append(row);}
}
function safeConfiguration(value){const clean={};if(!value||typeof value!=='object'||Array.isArray(value))return clean;for(const [key,item] of Object.entries(value)){if(/password|secret|token|credential|authorization|cookie|key/i.test(key))continue;if(item===null||['string','number','boolean'].includes(typeof item))clean[key]=item;}return clean;}
function renderConfiguration(data){const section=el('configSection'),container=el('configuration'),config=safeConfiguration(data.status?.configuration||data.status?.config||data.configuration||data.config),rows=Object.entries(config);section.hidden=!rows.length;container.replaceChildren();for(const [key,value] of rows){const row=node('div');row.append(node('dt',metricLabel(key)),node('dd',formatValue(value)));container.append(row);}}
function renderBackups(data){
 const container=el('backups'),backups=Array.isArray(data.status?.backups)?data.status.backups:Array.isArray(data.backups)?data.backups:[];container.replaceChildren();
 if(!backups.length){container.append(node('p','Копий пока нет','empty'));return;}
 for(const backup of backups){const item=node('article',undefined,'list-item'),name=backup?.name||backup?.filename||backup?.id||'Резервная копия',status=backup?.status||backup?.state||'unknown';item.append(node('h3',name),node('p',`${labelForState(status)} · ${formatDate(backup?.created_at||backup?.at||backup?.timestamp)}`));if(backup?.size!==undefined||backup?.size_bytes!==undefined)item.append(node('p',`Размер: ${formatBytes(backup.size??backup.size_bytes)}`));container.append(item);}
}
function renderJobs(data){
 const container=el('jobs'),jobs=Array.isArray(data.jobs)?data.jobs:[];container.replaceChildren();
 if(!jobs.length){container.append(node('p','Операций пока нет','empty'));return;}
 for(const job of jobs){const item=node('article',undefined,'list-item'),action=job?.action||job?.type||'operation',target=job?.service?` · ${serviceTitles[normalizedState(job.service)]||job.service}`:'';item.append(node('h3',`${action}${target}`),node('p',`${labelForState(job?.status||job?.state)} · ${formatDate(job?.completed_at||job?.finished_at||job?.created_at||job?.queued_at||job?.at)}`));if(job?.error)item.append(node('p',`Ошибка: ${typeof job.error==='string'?job.error:'Операция не выполнена'}`));if(job?.id)item.append(node('p',`ID: ${job.id}`));container.append(item);}
}
function renderEvents(data){
 const container=el('events'),events=Array.isArray(data.events)?data.events:[];container.replaceChildren();
 if(!events.length){container.append(node('p','Событий пока нет','empty'));return;}
 for(const event of [...events].reverse()){const item=node('article',undefined,`timeline-item ${eventTone(event)}`);item.append(node('h3',eventTitle(event)));const detail=eventDetail(event);if(detail!=='—')item.append(node('p',detail));item.append(node('p',eventTime(event)));container.append(item);}
 renderAlerts(events);
}
function isAlert(event){return event?.alert===true||eventTone(event)==='bad'||eventTone(event)==='warn';}
function renderAlerts(events){
 const alerts=events.map((event,index)=>({event,key:eventKey(event,index)})).filter(item=>isAlert(item.event)&&!dismissedAlertKeys.has(item.key)).slice(0,5),section=el('alertsSection'),container=el('alerts');container.replaceChildren();
 section.hidden=!alerts.length;
 for(const {event} of alerts){const item=node('div',undefined,'alert-item');item.append(node('strong',eventTitle(event)),node('span',`${eventDetail(event)} · ${eventTime(event)}`));container.append(item);}
}
function applySettings(data,force=false){
 savedSettings={backup_enabled:Boolean(data.settings?.backup_enabled),backup_hour_utc:Number.isInteger(data.settings?.backup_hour_utc)?data.settings.backup_hour_utc:0};
 if(!settingsDirty||force){el('backupEnabled').checked=savedSettings.backup_enabled;el('backupHour').value=String(savedSettings.backup_hour_utc);settingsDirty=false;}
 const disabled=!data.enabled;for(const control of el('settingsForm').elements)control.disabled=disabled;el('runBackup').disabled=!backupAllowed();
}
function render(data){latestSnapshot=data;renderSummary(data);renderServices(data);renderMetrics(data);renderConfiguration(data);renderBackups(data);renderJobs(data);renderEvents(data);applySettings(data);el('disabledNotice').hidden=data.enabled!==false;el('exportReport').disabled=false;dispatchEvent(new Event('operations-rendered'));}
async function loadOperations({announce=false}={}){
 if(pollInFlight||document.hidden)return;pollInFlight=true;if(announce)el('pageStatus').textContent='Обновляем состояние…';
 try{const data=await api('/api/v1/admin/operations');render(data);el('pageStatus').textContent=`Обновлено ${new Date().toLocaleTimeString('ru-RU')}`;el('pageStatus').className='page-status';}
 catch(error){el('pageStatus').textContent=`Не удалось обновить: ${error.message}. Последние показанные данные могут быть устаревшими.`;el('pageStatus').className='page-status error';if(latestSnapshot){latestSnapshot={...latestSnapshot,stale:true};render(latestSnapshot);}}
 finally{pollInFlight=false;}
}
async function queueJob(action,service,button){
 button.disabled=true;el('pageStatus').textContent='Ставим операцию в очередь…';
 try{const body={action};if(service)body.service=service;const job=await api('/api/v1/admin/operations/jobs','POST',body);el('pageStatus').textContent=`Операция поставлена в очередь${job?.id?` · ID ${job.id}`:''}`;await loadOperations();}
 catch(error){el('pageStatus').textContent=error.message;el('pageStatus').className='page-status error';}
 finally{button.disabled=action==='backup'?!backupAllowed():!controlsAllowed();}
}
function sanitizeExport(value){
 if(Array.isArray(value))return value.map(sanitizeExport);
 if(value&&typeof value==='object'){const clean={};for(const [key,item] of Object.entries(value)){if(/password|secret|token|credential|authorization|cookie/i.test(key))continue;clean[key]=sanitizeExport(item);}return clean;}
 return value;
}
function exportReport(){
 if(!latestSnapshot)return;const report=sanitizeExport({...latestSnapshot,exported_at:new Date().toISOString()}),url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=`operations-${new Date().toISOString().replaceAll(':','-')}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

el('backupEnabled').addEventListener('change',()=>{settingsDirty=true;el('settingsStatus').textContent='Есть несохранённые изменения';});
el('backupHour').addEventListener('input',()=>{settingsDirty=true;el('settingsStatus').textContent='Есть несохранённые изменения';});
el('resetSettings').addEventListener('click',()=>{if(!savedSettings)return;el('backupEnabled').checked=savedSettings.backup_enabled;el('backupHour').value=String(savedSettings.backup_hour_utc);settingsDirty=false;el('settingsStatus').textContent='Изменения отменены';});
el('settingsForm').addEventListener('submit',async event=>{
 event.preventDefault();const hour=Number(el('backupHour').value);if(!Number.isInteger(hour)||hour<0||hour>23){el('settingsStatus').textContent='Укажите целый час от 0 до 23';el('settingsStatus').className='form-status error';return;}
 const button=el('saveSettings');button.disabled=true;
 try{const result=await api('/api/v1/admin/operations/settings','PATCH',{backup_enabled:el('backupEnabled').checked,backup_hour_utc:hour}),returned=result?.settings||result;if(returned&&typeof returned.backup_enabled==='boolean'&&Number.isInteger(returned.backup_hour_utc)){latestSnapshot={...latestSnapshot,settings:returned};savedSettings={backup_enabled:returned.backup_enabled,backup_hour_utc:returned.backup_hour_utc};}else savedSettings={backup_enabled:el('backupEnabled').checked,backup_hour_utc:hour};settingsDirty=false;el('backupEnabled').checked=savedSettings.backup_enabled;el('backupHour').value=String(savedSettings.backup_hour_utc);el('settingsStatus').textContent='Настройки сохранены';el('settingsStatus').className='form-status';await loadOperations();}
 catch(error){el('settingsStatus').textContent=error.message;el('settingsStatus').className='form-status error';}
 finally{button.disabled=!latestSnapshot?.enabled;}
});
el('runBackup').addEventListener('click',()=>queueJob('backup',null,el('runBackup')));
el('refresh').addEventListener('click',()=>loadOperations({announce:true}));
el('exportReport').addEventListener('click',exportReport);
el('exportConfigurationXml').addEventListener('click',()=>{
 if(!latestSnapshot){el('pageStatus').textContent='Сначала дождитесь снимка конфигурации';return;}
 const xml=document.implementation.createDocument(null,'trainer112-configuration'),root=xml.documentElement;root.setAttribute('version','1');root.setAttribute('purpose','sanitized-export');
 const append=(parent,key,value)=>{const entry=xml.createElement('entry');entry.setAttribute('key',key);parent.append(entry);if(value&&typeof value==='object'){for(const [child,item] of Object.entries(value))append(entry,child,item);}else entry.textContent=value==null?'':String(value);};
 append(root,'deployment',sanitizeExport(latestSnapshot.status?.configuration||{}));append(root,'backup',latestSnapshot.settings||{});
 const url=URL.createObjectURL(new Blob(['<?xml version="1.0" encoding="UTF-8"?>\n',new XMLSerializer().serializeToString(xml)],{type:'application/xml'}));const link=document.createElement('a');link.href=url;link.download='trainer112-configuration.xml';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
});
el('dismissAlerts').addEventListener('click',()=>{const events=Array.isArray(latestSnapshot?.events)?latestSnapshot.events:[];events.forEach((event,index)=>{if(isAlert(event))dismissedAlertKeys.add(eventKey(event,index));});renderAlerts(events);});
el('logout').addEventListener('click',async()=>{try{await api('/api/v1/auth/logout','POST');}finally{location.replace('/login');}});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)loadOperations({announce:true});});
setInterval(()=>loadOperations(),15000);

(async()=>{
 try{const me=await api('/api/v1/auth/me');if(me.role!=='admin'){location.replace('/portal');return;}el('identity').textContent=`${me.display_name||me.username} · Администратор`;await loadOperations({announce:true});}
 catch(error){el('pageStatus').textContent=error.message;el('pageStatus').className='page-status error';}
})();
