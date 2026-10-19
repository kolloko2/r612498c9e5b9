'use strict';
// Structured editors preserve unexposed keys; raw JSON remains an optional view.
const ddsFields={
 prefilled_card:[['city','Город'],['district','Округ'],['area','Район'],['street','Улица'],['house','Дом'],['building','Корпус'],['structure','Строение'],['apartment','Квартира'],['caller_name','Заявитель'],['phone','Телефон заявителя'],['incident_type','Итоговый тип происшествия'],['description','Описание','text'],['services','Службы-получатели, по одной в строке','list'],['service_phones','Телефоны служб: служба = номер','pairs']],
 updates:[['id','Код доклада'],['after_seconds','Задержка, с (этап бригады — от назначения; прочее — от поступления)','number',40],['source','Кто докладывает'],['text','Содержание доклада','text'],['unlocks_status','Разрешаемый статус','status']],
 crew_options:[['id','Номер бригады'],['leader','Старший группы'],['phone','Телефон бригады']],
 phone_contacts:[['id','Код контакта'],['name','Имя и должность'],['role','Роль собеседника','role'],['number','Учебный номер'],['crew_id','Номер бригады (для её руководителя обязателен)'],['facts','Что известно собеседнику, по одной записи в строке','list'],['required_keywords','Что ученик должен передать в разговоре, по одной фразе в строке','list'],['required','Разговор обязателен для завершения','boolean',false]],
 dds_expectation:[['should_accept','Карточку следует принять','boolean',true],['brief_service','Кому необходимо доложить'],['expected_crew_id','Правильная бригада'],['leadership_decision_required','Нужно решение руководителя','boolean',false],['update_response_limit_seconds','Время реакции на доклад, секунд','number',90],['refusal_keywords','Обязательные слова причины отказа','list'],['brief_keywords','Обязательные факты доклада','list'],['result_keywords','Обязательные слова результата работ','list'],['expected_corrections','Ошибки карточки 112: поле = правильное значение (ДДС сообщает в 112)','pairs'],['correction_evidence','Что сообщит бригада с места: поле = текст доклада','pairs'],['brief_required_fields','Обязательные поля доклада (не выбрано — стандартный набор)','fields'],['check_weights','Веса проверок: код = число','pairs'],['pass_percent','Проходной процент','number',100]]
};
const reportFieldNames={city:'Город',street:'Улица',house:'Дом',building:'Корпус',structure:'Строение',apartment:'Квартира',entrance:'Подъезд',floor:'Этаж',object:'Объект',incident_type:'Происшествие',injured:'Пострадавшие'};
const structuredEditors=[];
ddsFields.dds_expectation.push(['update_keywords','Факты в комментарии к докладу: код доклада = фраза; другая фраза (каждый доклад с новой строки)','phrasePairs']);
ddsFields.prefilled_card.push(['object','Объект'],['recipient_affiliations','Получатели по территории и подчинённости: area = ДДС района; district = ДДС округа; department = ведомственная ДДС (каждый с новой строки)','pairs']);
function buildDdsEditors(){
 for(const [key,schema] of Object.entries(ddsFields)){
  const source=document.querySelector(`[data-key="${key}"]`),label=source.closest('label');
  const section=document.createElement('section'),heading=document.createElement('h3'),body=document.createElement('div'),advanced=document.createElement('details'),summary=document.createElement('summary');
  heading.textContent=label.firstChild.textContent.replace(' (JSON)','');summary.textContent='Расширенный формат JSON';
  section.className='structured-editor';label.before(section);advanced.append(summary,label);section.append(heading,body,advanced);
  const isList=['updates','crew_options','phone_contacts'].includes(key);
  function readSource(){return source.value.trim()?JSON.parse(source.value):(isList?[]:{});}
  function storeValue(value){source.value=JSON.stringify(value,null,2);source.dispatchEvent(new Event('input',{bubbles:true}));}
  function render(){
   body.replaceChildren();let value;
   try{value=readSource()||{};if(isList&&!Array.isArray(value))throw Error();}catch{body.textContent='Исправьте JSON в расширенном формате, затем закройте его.';return;}
   function fields(object,index){
    const block=document.createElement('div');block.className='structured-fields';
    for(const [name,title,type='string',fallback=''] of schema){
     const wrap=document.createElement('label');wrap.textContent=title;
     const input=document.createElement(['status','fields','role'].includes(type)?'select':['list','pairs','phrasePairs','text'].includes(type)?'textarea':'input');
     if(type==='role')for(const [role,title] of Object.entries({superior:'Начальник дежурной смены',organization_head:'Руководитель организации',response_chief:'Руководитель реагирования',crew_leader:'Руководитель бригады',crew_specialist:'Специалист бригады',applicant:'Заявитель (номер из карточки)'}))input.add(new Option(title,role));
     const original=object[name]??fallback;
     if(type==='status')for(const status of ['', 'Начало реагирования','Прибытие','Проведение работ','Работы завершены','Отказ от выполнения работ'])input.add(new Option(status||'Не открывает статус',status));
     if(type==='fields'){input.multiple=true;input.size=6;for(const [field,label] of Object.entries(reportFieldNames))input.add(new Option(label,field,false,(Array.isArray(original)?original:[]).includes(field)));}
     else if(type==='boolean'){input.type='checkbox';input.checked=!!original;}
     else{if(type==='number'){input.type='number';input.min=name==='pass_percent'?'0':'5';input.max=name==='pass_percent'?'100':name==='after_seconds'?'3600':'1800';}input.value=type==='list'?(Array.isArray(original)?original.join('\n'):''):type==='phrasePairs'?Object.entries(original||{}).map(([k,v])=>`${k} = ${v.join('; ')}`).join('\n'):type==='pairs'?Object.entries(original||{}).map(([k,v])=>`${k} = ${v}`).join('\n'):original;}
     const commit=()=>{
      let next;
      try{next=readSource()||(isList?[]:{});}catch{return;}
      const target=isList?next[index]:next;if(!target)return;
      if(type==='fields')target[name]=[...input.selectedOptions].map(option=>option.value);
      else if(type==='boolean')target[name]=input.checked;
      else if(type==='number'){if(!input.value||!input.checkValidity())return;target[name]=Number(input.value);}
      else if(type==='list')target[name]=input.value.split('\n').map(v=>v.trim()).filter(Boolean);
      else if(type==='phrasePairs'){
       const rows=input.value.split('\n').filter(v=>v.trim());
       if(rows.some(v=>v.indexOf('=')<1||!v.slice(v.indexOf('=')+1).trim())){input.setCustomValidity('Каждая строка: код доклада = обязательная фраза; другая фраза');return;}
       input.setCustomValidity('');target[name]=Object.fromEntries(rows.map(v=>[v.slice(0,v.indexOf('=')).trim(),v.slice(v.indexOf('=')+1).split(';').map(s=>s.trim()).filter(Boolean)]));
      }else if(type==='pairs'){
       const pairs=input.value.split('\n').filter(v=>v.trim());
       if(pairs.some(v=>v.indexOf('=')<1)){input.setCustomValidity('Каждая строка: название = значение');return;}
       input.setCustomValidity('');target[name]=Object.fromEntries(pairs.map(v=>[v.slice(0,v.indexOf('=')).trim(),name==='check_weights'?Number(v.slice(v.indexOf('=')+1).trim()):v.slice(v.indexOf('=')+1).trim()]));
      }else target[name]=input.value.trim();
      storeValue(next);
     };
     input.oninput=commit;input.onchange=commit;
     wrap.append(input);block.append(wrap);
    }
    if(isList){const remove=document.createElement('button');remove.type='button';remove.textContent='Удалить '+(key==='updates'?'доклад':key==='phone_contacts'?'контакт':'бригаду');remove.onclick=()=>{const next=readSource();next.splice(index,1);storeValue(next);render();};block.append(remove);}
    body.append(block);
   }
   if(isList){value.forEach(fields);const add=document.createElement('button');add.type='button';add.textContent=key==='updates'?'Добавить доклад':key==='phone_contacts'?'Добавить контакт':'Добавить бригаду';add.disabled=key==='phone_contacts'&&value.length>=10;add.onclick=()=>{const next=readSource();if(key==='phone_contacts'&&next.length>=10)return;next.push(key==='phone_contacts'?{id:'contact_'+crypto.randomUUID().slice(0,8),name:'',role:'organization_head',number:'',facts:[],required_keywords:[],required:false}:key==='updates'?{id:'report_'+crypto.randomUUID().slice(0,8),after_seconds:40,source:'Старший группы',text:'',unlocks_status:''}:{id:'',leader:'',phone:''});storeValue(next);render();};body.append(add);}else fields(value);
  }
  advanced.addEventListener('toggle',()=>{if(!advanced.open)render();});structuredEditors.push(render);render();
 }
}
buildDdsEditors();
addEventListener('scenario-filled',()=>structuredEditors.forEach(render=>render()));
