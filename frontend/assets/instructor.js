'use strict';
const el=id=>document.getElementById(id);
let scenarios=[],revision=0,dirty=false,busy=false,loadedId='';
const fieldNames={caller_name:'Имя заявителя',street:'Улица',house:'Дом',apartment:'Квартира',description:'Описание',incident_type:'Итоговый тип происшествия',injured:'Пострадавшие',no_access:'Нет доступа',services:'Службы'};
function status(text,error=false){el('status').textContent=text;el('status').className=error?'error':'';}
function changed(){dirty=true;status('Есть несохранённые изменения');}
async function api(path,method='GET',body){const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Voice-UI':'1'},...(body===undefined?{}:{body:JSON.stringify(body)})});if(response.status===401)location.replace('/login');if(response.status===403)location.replace('/portal');const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Проверьте критерии: поля, режимы, допустимые ответы и веса.');return data;}
function control(labelText,input){const label=document.createElement('label');input.setAttribute('aria-label',labelText);label.append(document.createTextNode(labelText),input);return label;}
function input(type,value){const node=document.createElement('input');node.type=type;node.value=value;return node;}
function addCriterion(value={}){
 if(el('criteria').children.length>=30){status('Допускается не более 30 критериев',true);return;}
 const row=document.createElement('section');row.className='criterion';row.dataset.id=value.id||'c_'+crypto.randomUUID().replaceAll('-','');
 const label=input('text',value.label||'');label.required=true;label.maxLength=120;label.dataset.key='label';
 const field=document.createElement('select');field.dataset.key='field';for(const [key,name] of Object.entries(fieldNames))field.add(new Option(name,key));
 if(value.field&&!fieldNames[value.field])field.add(new Option(value.field,value.field));field.value=value.field||'caller_name';
 const mode=document.createElement('select');mode.dataset.key='mode';for(const [key,name] of Object.entries({equals:'Совпадение',contains_all:'Все фрагменты',set_equals:'Точный набор'}))mode.add(new Option(name,key));mode.value=value.mode||'equals';
 const weight=input('number',value.weight||1);weight.min='1';weight.max='100';weight.required=true;weight.dataset.key='weight';
 const expected=document.createElement('textarea');expected.required=true;expected.maxLength=20000;expected.dataset.key='expected';expected.value=(value.expected||[]).join('\n');expected.placeholder='Каждый ответ или фрагмент — с новой строки. Для признаков: true или false.';
 const answers=control('Допустимые ответы / фрагменты / службы',expected);answers.className='answers';
 const remove=document.createElement('button');remove.type='button';remove.textContent='Удалить критерий';remove.onclick=()=>{row.remove();changed();};
 field.onchange=()=>{mode.value=field.value==='services'?'set_equals':'equals';changed();};
 row.append(control('Название критерия',label),control('Поле карточки',field),control('Сравнение',mode),answers,control('Вес',weight),remove);row.addEventListener('input',changed);el('criteria').append(row);
}
async function load(id){
 busy=true;el('editor').disabled=true;el('save').disabled=true;el('scenario').disabled=true;
try{const result=await api('/api/v1/instructor/scenarios/'+encodeURIComponent(id)+'/rubric');loadedId=id;revision=result.revision;const s=scenarios.find(x=>x.id===id);el('scenarioContext').textContent=s?`${s.incident}\n${s.location}`:'';el('title').value=result.rubric?.title||s?.title||'';el('limit').value=result.rubric?.time_limit_seconds||180;el('responseLimit').value=result.rubric?.response_limit_seconds||30;el('criteria').replaceChildren();for(const c of result.rubric?.criteria||[])addCriterion(c);dirty=false;el('revision').textContent='Версия '+revision;status(result.rubric?'Эталон загружен':'Эталон ещё не задан. Добавьте критерии и сохраните.');}
 finally{busy=false;el('editor').disabled=false;el('save').disabled=!loadedId;el('scenario').disabled=false;}
}
async function guarded(action){try{await action();}catch(e){status(e.message,true);}}
el('scenario').onchange=()=>{const id=el('scenario').value;if(dirty&&!confirm('Оставить несохранённые изменения?')){el('scenario').value=loadedId;return;}guarded(async()=>{try{await load(id);}catch(e){el('scenario').value=loadedId;throw e;}});};
el('add').onclick=()=>{addCriterion();changed();};
el('reload').onclick=()=>{if(!busy&&(!dirty||confirm('Отменить несохранённые изменения?')))guarded(()=>load(loadedId));};
el('title').oninput=changed;el('limit').oninput=changed;
el('rubricForm').onsubmit=event=>{event.preventDefault();if(busy||!loadedId)return;guarded(async()=>{
 const criteria=[...el('criteria').children].map(row=>{const read=key=>row.querySelector(`[data-key="${key}"]`).value;return {id:row.dataset.id,label:read('label'),field:read('field'),mode:read('mode'),weight:Number(read('weight')),expected:read('expected').split('\n').map(v=>v.trim()).filter(Boolean)};});
 if(!criteria.length)throw Error('Добавьте хотя бы один критерий');
 busy=true;el('editor').disabled=true;el('save').disabled=true;el('scenario').disabled=true;
 try{const result=await api('/api/v1/instructor/scenarios/'+encodeURIComponent(loadedId)+'/rubric','PUT',{revision,rubric:{title:el('title').value,time_limit_seconds:Number(el('limit').value),response_limit_seconds:Number(el('responseLimit').value),criteria}});revision=result.revision;dirty=false;el('revision').textContent='Версия '+revision;status('Эталон сохранён. Он будет использован в новых занятиях.');}
 finally{busy=false;el('editor').disabled=false;el('save').disabled=false;el('scenario').disabled=false;}
});};
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
guarded(async()=>{scenarios=await api('/api/scenarios');el('scenario').replaceChildren();for(const s of scenarios)el('scenario').add(new Option(s.title,s.id));if(scenarios.length)await load(scenarios[0].id);else{el('save').disabled=true;status('Нет доступных сценариев.');}});
