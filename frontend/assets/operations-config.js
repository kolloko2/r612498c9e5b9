'use strict';
let runtimeXml='',importedConfiguration=null;
const runtimeLabels={sip_external_address:'Объявляемый IP SIP',sip_local_net:'Локальная сеть SIP (CIDR)',allowed_extensions:'Разрешённые номера через запятую',max_calls:'Лимит звонков',postgres_max_connections:'PostgreSQL: соединения',postgres_shared_buffers_mb:'PostgreSQL: shared_buffers, МиБ',backend_memory_mb:'Backend: память, МиБ',voice_memory_mb:'Voice: память, МиБ',backend_cpus:'Backend: CPU',voice_cpus:'Voice: CPU',llm_profile:'Профиль языковой модели'};
// Профили описаны на сервере; здесь только подписи для администратора.
const llmProfiles=[['mock','Без модели — детерминированные ответы'],['standard','Стандартная (CPU) — рекомендуемая'],['accelerated','Ускоренная — требует видеокарту']];
async function loadRuntime(){
 try{const data=await api('/api/v1/admin/operations/configuration');runtimeXml=data.xml;el('runtimeFields').replaceChildren();
 for(const [key,value] of Object.entries(data.values)){const label=node('label',runtimeLabels[key]||key);let input;if(key==='llm_profile'){input=document.createElement('select');for(const [id,title] of llmProfiles)input.add(new Option(title,id));}else{input=document.createElement('input');input.type=typeof value==='number'?'number':'text';}input.name=key;input.value=value;input.required=true;label.append(input);el('runtimeFields').append(label);}}
 catch(error){el('runtimeStatus').textContent=error.message;}
}
async function applyRuntime(configuration){
 if(!confirm('Подтверждаете обслуживание? PostgreSQL, Backend и голосовые службы будут перезапущены. Активные занятия могут прерваться.'))return;
 try{const job=await api('/api/v1/admin/operations/jobs','POST',{action:'configure',configuration});el('runtimeStatus').textContent='Настройки поставлены в очередь: '+job.id;importedConfiguration=null;el('applyRuntimeXml').hidden=true;}
 catch(error){el('runtimeStatus').textContent=error.message;}
}
el('runtimeForm').onsubmit=event=>{event.preventDefault();const values={};for(const input of el('runtimeFields').querySelectorAll('input'))values[input.name]=input.type==='number'?Number(input.value):input.value;applyRuntime(values);};
el('downloadRuntimeXml').onclick=()=>{if(!runtimeXml)return;const url=URL.createObjectURL(new Blob([runtimeXml],{type:'application/xml'})),link=node('a');link.href=url;link.download='trainer112-settings.xml';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
el('runtimeXml').onchange=async()=>{
 importedConfiguration=null;el('applyRuntimeXml').hidden=true;const file=el('runtimeXml').files[0];if(!file)return;
 try{if(file.size>32768)throw Error('Максимум 32 КиБ');const data=await api('/api/v1/admin/operations/configuration/preview','POST',{xml:await file.text()});importedConfiguration=data.configuration;el('runtimePreview').textContent=data.changes.map(change=>`${runtimeLabels[change.key]||change.key}: ${change.before} → ${change.after}`).join('\n')||'Нет отличий';el('applyRuntimeXml').hidden=!data.changes.length;}
 catch(error){el('runtimeStatus').textContent=error.message;}
};
el('applyRuntimeXml').onclick=()=>{if(importedConfiguration)applyRuntime(importedConfiguration);};
el('loadServiceLogs').onclick=async()=>{try{const data=await api('/api/v1/admin/operations/logs');el('serviceLogs').textContent=data.available?data.lines.join('\n'):'Снимок журналов недоступен';}catch(error){el('serviceLogs').textContent=error.message;}};
el('installUpdate').onclick=()=>{if(confirm('Установить одобренное обновление из deploy/updates? Службы будут перезапущены.'))queueJob('update',null,el('installUpdate'));};
loadRuntime();
