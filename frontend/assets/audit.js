'use strict';
const auditElement=id=>document.getElementById(id);
let auditSnapshot=null;
auditElement('day').value=new Date().toISOString().slice(0,10);
async function loadAudit(){
 auditElement('status').textContent='Загрузка…';
 try{
  const response=await fetch('/api/v1/admin/audit?day='+encodeURIComponent(auditElement('day').value)+'&limit=200&source='+auditElement('source').value);
  if(response.status===401){location.replace('/login');return;}
  if(!response.ok)throw Error(response.status===403?'Доступ только администратору':'Не удалось прочитать журнал');
  auditSnapshot=await response.json();
  auditElement('status').textContent=!auditSnapshot.enabled?'Журнал не подключён':auditSnapshot.failed?'Ошибка записи журнала — требуется вмешательство администратора':`Получено ${auditSnapshot.events.length} записей`;
  auditElement('rows').replaceChildren();
  for(const event of [...auditSnapshot.events].reverse()){
   const row=document.createElement('tr');
   for(const value of [new Date(event.at*1000).toLocaleString('ru-RU'),[event.actor_id,event.role].filter(Boolean).join(' / ')||event.actor||'Не определён',`${event.method} ${event.route||'Начало запроса'}`,event.phase==='started'?'Начат':`${event.status} · ${event.elapsed_ms} мс`,event.request_id]){const cell=document.createElement('td');cell.textContent=value;row.append(cell);}
   auditElement('rows').append(row);
  }
  auditElement('download').disabled=false;
 }catch(error){auditElement('status').textContent=error.message;}
}
auditElement('filter').onsubmit=event=>{event.preventDefault();loadAudit();};
auditElement('download').onclick=()=>{if(!auditSnapshot)return;const url=URL.createObjectURL(new Blob([JSON.stringify(auditSnapshot,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`security-audit-${auditSnapshot.day||'snapshot'}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
loadAudit();
