// Standalone ARM browser regression with intercepted HTTP only: no Backend,
// database, external network, notification delivery, or physical printing.
const playwright=require('playwright/test'),{expect}=playwright;
// Движок выбирается BROWSER_ENGINE: chromium покрывает Chrome и Яндекс.Браузер,
// firefox — второй движок из требований к совместимости.
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',shots=path.join(root,'..','tmp');
const now='2026-09-15T09:00:00+00:00';
const blank={country:'Россия',region:'Москва',city:'Москва',object:'',district:'ЦАО',area:'Тверской',street:'Учебная',house:'10',building:'',structure:'',apartment:'5',entrance:'',floor:'',code:'',address_note:'Учебный адрес, вход со двора',caller_name:'Учебный заявитель',caller_status:'очевидец',phone:'+7 000 000-00-01',supplied_phone:'',scene_phone:'',description:'Синтетическое сообщение о задымлении',incident_type:'Учебное задымление',classifier_group:'',classifier_features:[],classifier_id:'',classifier_version:'test-v1',injured:false,refused:false,no_access:false,no_contact:false,interrupted:false,new_medical_help:false,services:['Служба 101']};
const cards=[
 {id:'arm-card-1',number:910101,title:'Учебное задымление',scenario_id:'arm-test',created_at:now,saved_at:now,status:'В работе',incident_status:'Зарегистрирована',revision:1,registration:{operator:'Учебный оператор',workstation:'Учебное АРМ'},card:{...blank,emergency:true,important:true,bookmarked:true,classifier_features:['Дым','Учебный объект']},events:[],service_states:{'Служба 101':{status:'Добавлена',added_at:now}},allowed_service_statuses:{'Служба 101':['Принята','Не принята']},notifications:[],linked_cards:[],transport:'text',messages:[],assessment_enabled:false},
 {id:'arm-card-2',number:910102,title:'Учебная медицина',scenario_id:'arm-test-2',created_at:now,saved_at:now,status:'В работе',incident_status:'Зарегистрирована',revision:1,card:{...blank,district:'САО',area:'Аэропорт',street:'Вторая учебная',house:'20',description:'Вторая собственная учебная карточка',incident_type:'Учебное обращение',services:[]},events:[],service_states:{},allowed_service_statuses:{},notifications:[],linked_cards:[],transport:'text',messages:[],assessment_enabled:false},
];
let serviceBody=null,notificationBody=null,linkBody=null,forwardBody=null,processedCalls=0;
const copy=value=>JSON.parse(JSON.stringify(value));
function card(id){return cards.find(item=>item.id===id);}
function linkView(item){return {id:item.id,number:item.number,incident_type:item.card.incident_type};}
async function serve(page){
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  if(p.startsWith('/api/')){
   let data,status=200;
   if(p==='/api/v1/auth/me')data={id:'student-test',role:'student',display_name:'Учебный студент',active:true};
   else if(p==='/api/v1/student/softphone')data={enabled:false,reason:'Номер не назначен'};
   else if(p==='/api/v1/health')data={status:'ok',provider:'mock'};
   else if(p==='/api/v1/student/classifier')data={version:'test-v1',groups:[],records:[]};
   else if(p==='/api/v1/student/routing/catalog')data={rules_version:'full-v2',services:['Служба 101','Служба 102','Служба 103'],flags:[{id:'new_medical_help',label:'Нужна новая медпомощь'}]};
   else if(p==='/api/v1/student/routing/preview')data={rules_version:'full-v2',source_row:null,suggestions:[],excluded:[],unresolved:[],warnings:[]};
   else if(p==='/api/v1/student/assignments'||p==='/api/v1/student/lessons')data=[];
   else if(p==='/api/v1/student/sessions')data=cards.map(copy);
   else if(p==='/api/v1/student/inbox/poll')data=[];
   else {
    const match=p.match(/^\/api\/v1\/student\/sessions\/([^/]+)(?:\/(card|services|notifications|links|forward|processed))?$/);
    if(!match)throw Error('Unexpected API '+request.method()+' '+p);
    const item=card(match[1]);if(!item)throw Error('Unknown fixture card '+match[1]);
    const action=match[2];
    if(action==='card'){
     const body=request.postDataJSON();if(body.revision!==item.revision)throw Error('Stale fixture revision');item.card=body.card;item.revision++;item.saved_at=now;
    }else if(action==='services'){
     serviceBody=request.postDataJSON();if(!serviceBody.message_id)throw Error('Service action omitted message_id');item.service_states[serviceBody.service]={status:serviceBody.status,comment:serviceBody.comment,at:now};item.allowed_service_statuses[serviceBody.service]=['Начало реагирования','Прибытие','Проведение работ','Работы завершены','Отказ от выполнения работ'];item.events.push({type:'service.updated',at:now,detail:{service:serviceBody.service,status:serviceBody.status,comment:serviceBody.comment}});
    }else if(action==='notifications'){
     notificationBody=request.postDataJSON();if(!notificationBody.message_id)throw Error('Notification omitted message_id');item.notifications.push({...notificationBody,at:now,operator:'Учебный оператор'});
    }else if(action==='links'){
     linkBody=request.postDataJSON();const target=card(linkBody.target_id);if(!target)throw Error('Unknown link target');item.linked_cards=[linkView(target)];target.linked_cards=[linkView(item)];
    }else if(action==='forward'){
     forwardBody=request.postDataJSON();
     if(!forwardBody.message_id||!forwardBody.reason)throw Error('Forward payload incomplete');
     item.card.services.push(forwardBody.service);
     item.service_states[forwardBody.service]={status:'Добавлена',comment:forwardBody.reason,at:now};
     item.allowed_service_statuses[forwardBody.service]=['Принята','Не принята'];
     item.events.push({type:'card.forwarded',at:now,detail:forwardBody});
    }else if(action==='processed'){
     processedCalls++;item.processed_at=now;item.incident_status='Отработана';
    }
    data=copy(item);
   }
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/'?'student.html':p==='/map'?'map.html':p.slice(1);if(file.includes('..'))throw Error('Unsafe test asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}
(async()=>{
 await fs.mkdir(shots,{recursive:true});
 const browser=await engine.launch({headless:true,...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
  await serve(page);await page.goto(origin);
  await expect(page.locator('tr[aria-label="Происшествие 910101"]')).toBeVisible();
  await page.locator('tr[aria-label="Происшествие 910101"]').click();await expect(page.locator('#cardNumber')).toContainText('910101');
  await page.locator('#edit').click();await expect(page.locator('#description')).toBeEditable();
  await page.locator('#description').fill('Синтетическое сообщение уточнено оператором');
  await page.locator('[data-field="latitude"]').fill('0');await page.locator('[data-field="longitude"]').fill('0');
  await page.screenshot({path:path.join(shots,'arm-edit.png')});
  await page.locator('#save').click();await expect(page.locator('#saveState')).toHaveText('Сохранено');
  await expect(page.locator('[data-field="latitude"]')).toHaveValue('0');await expect(page.locator('[data-field="longitude"]')).toHaveValue('0');
  if(cards[0].card.latitude!==0||cards[0].card.longitude!==0)throw Error('Coordinates not saved as numbers');
  await page.locator('#openMap').click();
  await expect(page.locator('#mapDialog')).toBeVisible();
  await expect(page.locator('#mapDialog iframe')).toHaveAttribute('src','/map?sid=arm-card-1');
  await page.locator('#mapDialog button').first().click();
  await expect(page.locator('#mapDialog')).toBeHidden();

  await expect(page.locator('#responseStatus option')).toHaveText(['Выберите статус…','Принята','Не принята']);
  await page.locator('#responseStatus').selectOption('Принята');await page.locator('#responseComment').fill('Принял учебное сообщение');await page.locator('#addResponse').click();
  await expect(page.locator('#serviceHistory')).toContainText('Принята');
  if(!serviceBody?.message_id||serviceBody.status!=='Принята'||serviceBody.service!=='Служба 101')throw Error('Invalid service action payload');

  await page.locator('#openNotification').click();await page.locator('#notificationDestination').fill('ПСЧ-1');await page.locator('#notificationPhone').fill('+7 000 101-01-01');await page.locator('#notificationRecipient').fill('Учебный диспетчер');await page.locator('#notificationComment').fill('Проверка формы телефонограммы');await page.locator('#saveNotification').click();
  await expect(page.locator('#notificationHistory')).toContainText('Учебный диспетчер');
  if(!notificationBody?.message_id||notificationBody.destination!=='ПСЧ-1'||notificationBody.phone!=='+7 000 101-01-01'||notificationBody.recipient!=='Учебный диспетчер'||notificationBody.comment!=='Проверка формы телефонограммы')throw Error('Invalid notification payload');

  // Перенаправление в другую службу — отдельное действие с обоснованием.
  await page.locator('#openForward').click();
  await expect(page.locator('#forwardDialog')).toBeVisible();
  await page.locator('#forwardService').selectOption('Служба 102');
  await page.locator('#forwardReason').fill('Требуется полиция, происшествие не нашего профиля');
  await page.locator('#forwardForm button[type="submit"]').click();
  await expect(page.locator('#forwardDialog')).toBeHidden();
  if(forwardBody?.service!=='Служба 102')throw Error('Invalid forward payload');
  if(!forwardBody?.reason?.includes('полиция'))throw Error('Forward reason lost');
  await expect(page.locator('#services')).toContainText('Служба 102');

  await page.locator('#openLinks').click();await page.locator('#linkTarget').selectOption('arm-card-2');await page.locator('#addLink').click();await expect(page.locator('#linkedCards')).toContainText('910102');
  if(linkBody?.target_id!=='arm-card-2')throw Error('Invalid card link payload');

  await page.evaluate(()=>{window.__armPrintCalls=0;window.print=()=>{window.__armPrintCalls++;};});await page.locator('#printCard').click();if(await page.evaluate(()=>window.__armPrintCalls)!==1)throw Error('Print action did not call window.print exactly once');
  await page.locator('#processed').click();await expect(page.locator('#incidentState')).toContainText('Отработана');await expect(page.locator('#finish')).toBeEnabled();
  if(processedCalls!==1||card('arm-card-1').status==='Завершена')throw Error('Processed action incorrectly finished the training session');
  await page.screenshot({path:path.join(shots,'arm-savedcard.png')});

  await page.locator('#journal').evaluate(button=>button.click());await expect(page.locator('#journalPanel')).toBeVisible();await page.locator('#filters').evaluate(button=>button.click());await expect(page.locator('#advancedFilters')).toBeVisible();await expect(page.locator('#filters')).toHaveAttribute('aria-expanded','true');await page.locator('#filterDistrict').fill('ЦАО');await page.locator('#filterService').selectOption('Служба 101');await page.locator('#filterIncidentStatus').selectOption('Отработана');await page.locator('#filterIncidentType').fill('задымление');await page.locator('#filterFeatures').fill('Учебный объект');await page.locator('#filterAddress').fill('Учебная, д. 10');await page.locator('#filterRegion').fill('Москва');await page.locator('#filterCardNumber').fill('910101');await page.locator('#applyAdvanced').click();
  await expect(page.locator('tr[aria-label^="Происшествие"]')).toHaveCount(1);const row=page.locator('tr[aria-label="Происшествие 910101"]');await expect(row).toContainText('Учебный оператор');await expect(row).toContainText('Учебное АРМ');await expect(row).toContainText('ϟ');const toggle=row.locator('.row-toggle');await expect(toggle).toHaveAttribute('aria-expanded','false');await toggle.click();await expect(toggle).toHaveAttribute('aria-expanded','true');const inline=page.locator('#journal-detail-arm-card-1');await expect(inline).toBeVisible();await expect(inline).toContainText('Служба 101');await expect(inline).toContainText('Учебный заявитель');await expect(inline).toContainText('уточнено оператором');
  await page.screenshot({path:path.join(shots,'arm-journal.png')});
  await page.locator('#resetAdvanced').click();const legacyRow=page.locator('tr[aria-label="Происшествие 910102"]');await expect(legacyRow).toBeVisible();await expect(legacyRow.locator('td').nth(5)).toHaveText('—');await expect(legacyRow.locator('td').nth(6)).toHaveText('—');
  // Мобильная ширина рабочего места: без горизонтальной прокрутки.
  await page.setViewportSize({width:400,height:900});
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
  if(overflow>1)throw Error('Horizontal overflow at 400px: '+overflow);
  await page.screenshot({path:path.join(shots,'arm-mobile.png')});

  if(errors.length)throw Error(errors.join('\n'));
  console.log('ARM browser flow: PASS (mock HTTP, no database/network/printer)');
  console.log(['arm-edit.png','arm-savedcard.png','arm-journal.png'].map(name=>path.join(shots,name)).join('\n'));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
