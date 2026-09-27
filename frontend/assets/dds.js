const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const when=value=>value?new Date(value).toLocaleString('ru-RU'):'—';
const toast=text=>{const box=$('toast');box.textContent=text;box.classList.add('show');setTimeout(()=>box.classList.remove('show'),2600)};

async function api(path,method='GET',body){
 const response=await fetch('/api/v1/'+path,{method,headers:{'Content-Type':'application/json','X-Voice-UI':'1'},body:body?JSON.stringify(body):undefined});
 if(response.status===401){location.href='/login';throw Error('Требуется вход')}
 if(!response.ok){let detail='Ошибка запроса';try{detail=(await response.json()).detail||detail}catch{}throw Error(detail)}
 return response.status===204?null:response.json();
}

function address(card){return [card.region,card.city,card.district,card.area,card.street,card.house&&`д. ${card.house}`,card.building&&`корп. ${card.building}`,card.structure&&`стр. ${card.structure}`].filter(Boolean).join(', ')||'Адрес не указан'}

function drawRows(items){
 $('rows').replaceChildren();$('empty').hidden=items.length>0;$('summary').textContent=`Карточек: ${items.length}`;
 items.forEach(item=>{
  const card=item.card,tr=document.createElement('tr');
  tr.innerHTML=`<td class="${item.opened_at?'opened':'new'}">${item.opened_at?'Открыта':'Новая'}</td><td>${esc(card.number)}</td><td>${esc(when(item.sent_at))}</td><td>${esc(card.incident_type||card.title||'—')}</td><td class="address">${esc(address(card))}</td><td class="description" title="${esc(card.description)}">${esc(card.description||'—')}</td><td><button type="button">Открыть</button></td>`;
  tr.querySelector('button').onclick=()=>openCard(item.id);$('rows').append(tr);
 });
}

async function load(){
 try{const profile=$('profile').value;if(!profile)return;drawRows(await api(`instructor/dds/incoming?profile=${encodeURIComponent(profile)}&limit=100`))}catch(error){toast(error.message)}
}
let recipientsRevision=0;
async function loadRecipients(){try{const value=await api(`instructor/dds/profiles/${$('profile').value}/recipients`);recipientsRevision=value.revision;$('recipients').value=value.recipients.join('\n');}catch(error){toast(error.message);}}
$('recipientsForm').onsubmit=async event=>{event.preventDefault();try{const result=await api(`instructor/dds/profiles/${$('profile').value}/recipients`,'PUT',{revision:recipientsRevision,recipients:$('recipients').value.split('\n').map(v=>v.trim()).filter(Boolean)});recipientsRevision=result.revision;toast('Получатели сохранены');}catch(error){toast(error.message);}};

const field=(label,value,wide='')=>`<div class="field ${wide}"><b>${esc(label)}</b>${esc(value||'—')}</div>`;
async function openCard(id){
 try{
  const profile=$('profile').value;
  const item=await api(`instructor/dds/incoming/${id}/open`,'POST',{profile}),card=item.card;
  $('cardDetail').innerHTML=`<div class="detail"><div class="training-meta">Профиль: <b>${esc(item.profile_title)}</b></div><div class="detail-grid">${field('Номер карточки',card.number)}${field('Поступила',when(item.sent_at))}${field('Итоговый тип',card.incident_type)}${field('Сложность',card.difficulty)}${field('Адрес',address(card),'wide')}${field('Описание',card.description,'wide')}${field('Видимые службы реагирования',(card.visible_response_services||[]).join(', '),'wide')}${field('Информационные получатели вне полосы оповещения',(item.informational_recipients||[]).join(', ')||'Не заданы','wide')}${field('Цели занятия',card.learning_objectives,'wide')}</div></div>`;
  $('cardDialog').showModal();await load();
 }catch(error){toast(error.message)}
}

async function init(){
 try{
  const me=await api('auth/me');if(me.role!=='teacher')throw Error('Журнал ДДС доступен преподавателю');
  const profiles=await api('instructor/dds/profiles');profiles.forEach(profile=>$('profile').add(new Option(`${profile.title} · ${profile.incoming_count}`,profile.id)));
  // Открыть профиль, в который карточки действительно поступали.
  const busiest=profiles.reduce((best,p)=>p.incoming_count>(best?.incoming_count||0)?p:best,null);if(busiest)$('profile').value=busiest.id;await load();await loadRecipients();
 }catch(error){toast(error.message)}
}
$('profile').onchange=()=>{load();loadRecipients();};$('refresh').onclick=load;init();
