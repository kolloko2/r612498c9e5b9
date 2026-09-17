// Доклад дежурному службы в АРМ: intercepted HTTP only, без Backend и модели.
// Движок выбирается BROWSER_ENGINE (chromium по умолчанию, firefox поддержан).
const playwright=require('playwright/test'),{expect}=playwright;
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',shots=path.join(root,'..','tmp');
const now='2026-09-16T09:00:00+00:00';

const card={id:'brief-card',number:910301,title:'Учебный пожар',scenario_id:'brief-test',created_at:now,
 saved_at:now,status:'В работе',incident_status:'Зарегистрирована',revision:2,
 registration:{operator:'Диспетчер Учебный',workstation:'АРМ-05'},
 card:{country:'Россия',region:'Москва',city:'Москва',object:'',district:'САО',area:'Тимирязевский',
  street:'Дубнинская',house:'12',building:'',structure:'',apartment:'5',entrance:'',floor:'',code:'',
  address_note:'Во дворе',caller_name:'Иванова Елена Сергеевна',caller_status:'очевидец',
  phone:'+7 900 000-00-01',supplied_phone:'',scene_phone:'',description:'Горит балкон',
  incident_type:'Пожар в квартире',classifier_group:'',classifier_features:[],classifier_id:'',
  classifier_version:'test-v1',injured:false,refused:false,no_access:false,no_contact:false,
  interrupted:false,new_medical_help:false,services:['Служба 101','Служба 103']},
 events:[],service_states:{'Служба 101':{status:'Добавлена',added_at:now},'Служба 103':{status:'Добавлена',added_at:now}},
 allowed_service_statuses:{},notifications:[],linked_cards:[],transport:'text',messages:[],assessment_enabled:false};

// Состояние доклада на стороне «сервера».
let briefing=null,finishBody=null;
const spokenOf=()=>briefing.messages.filter(m=>m.role==='user').map(m=>m.content).join(' ').toLowerCase();
function report(){
 const said=spokenOf();
 const missing=[];
 if(!said.includes('дубнинская'))missing.push('Улица или ориентир');
 if(!said.includes('12'))missing.push('Дом');
 if(!said.includes('пожар'))missing.push('Тип происшествия');
 return {missing,complete:missing.length===0,checks:[]};
}

async function serve(page){
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  if(p.startsWith('/api/')){
   let data,status=200;
   if(p==='/api/v1/auth/me')data={id:'student-test',role:'student',display_name:'Диспетчер Учебный',active:true};
   else if(p==='/api/v1/health')data={status:'ok',provider:'mock'};
   else if(p==='/api/v1/student/classifier')data={version:'test-v1',groups:[],records:[]};
   else if(p==='/api/v1/student/routing/catalog')data={rules_version:'full-v2',services:['Служба 101'],flags:[]};
   else if(p==='/api/v1/student/assignments'||p==='/api/v1/student/lessons')data=[];
   else if(p==='/api/v1/student/sessions')data=[JSON.parse(JSON.stringify(card))];
   else if(p===`/api/v1/student/sessions/${card.id}/briefings`&&request.method()==='POST'){
    const body=request.postDataJSON();
    briefing={id:'brief-1',service:body.service,state:'open',voice:'baya',simulated:true,
     messages:[{role:'assistant',content:`Дежурный, ${body.service}. Слушаю вас.`,at:now}],report:null};
    briefing.report=report();
    data=briefing;status=201;
   }
   else if(p===`/api/v1/student/sessions/${card.id}/briefings/brief-1/messages`){
    briefing.messages.push({role:'user',content:request.postDataJSON().text,at:now});
    briefing.report=report();
    briefing.messages.push({role:'assistant',at:now,
     content:briefing.report.complete?'Информация принята.':'Уточните, пожалуйста: '+briefing.report.missing.join(', ').toLowerCase()+'.'});
    data=briefing;
   }
   else if(p===`/api/v1/student/sessions/${card.id}/briefings/brief-1/finish`){
    finishBody=request.postDataJSON();
    briefing.state='accepted';briefing.messages.push({role:'assistant',content:'Информация принята.',at:now});
    card.notifications=[{message_id:'m1',service:briefing.service,destination:briefing.service,phone:'',
     recipient:finishBody.recipient,comment:spokenOf(),at:now,operator:'Диспетчер Учебный'}];
    data=briefing;
   }
   else if(p===`/api/v1/student/sessions/${card.id}`)data=JSON.parse(JSON.stringify(card));
   else throw Error('Unexpected API '+request.method()+' '+p);
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/'?'student.html':p.slice(1);
  if(file.includes('..'))throw Error('Unsafe test asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}

(async()=>{
 await fs.mkdir(shots,{recursive:true});
 const browser=await engine.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
  await serve(page);await page.goto(origin);

  await page.locator('tr[aria-label="Происшествие 910301"]').click();
  await expect(page.locator('#cardNumber')).toContainText('910301');
  await page.locator('#openBriefing').click();
  await expect(page.locator('#briefingDialog')).toBeVisible();
  // Службы карточки предложены для вызова.
  await expect(page.locator('#briefingService option')).toHaveCount(2);

  await page.locator('#startBriefing').click();
  await expect(page.locator('#briefingTranscript')).toContainText('Дежурный, Служба 101. Слушаю вас.');
  await expect(page.locator('#briefingMissing')).toContainText('Ещё не названо');
  // Пока доклад неполный, завершение недоступно.
  await expect(page.locator('#briefingFinishForm')).toBeHidden();

  await page.locator('#briefingText').fill('Докладываю: улица Дубнинская');
  await page.locator('#sendBriefing').click();
  await expect(page.locator('#briefingTranscript')).toContainText('Уточните, пожалуйста');
  await expect(page.locator('#briefingMissing')).toContainText('Дом');

  await page.locator('#briefingText').fill('дом 12, пожар в квартире');
  await page.locator('#sendBriefing').click();
  await expect(page.locator('#briefingMissing')).toContainText('Сведения названы полностью');
  await expect(page.locator('#briefingFinishForm')).toBeVisible();
  await page.screenshot({path:path.join(shots,'briefing-dialog.png')});

  await page.locator('#briefingRecipient').fill('Дежурный смены Петров');
  await page.locator('#finishBriefing').click();
  await expect(page.locator('#briefingMissing')).toContainText('Доклад принят дежурным');
  if(finishBody?.recipient!=='Дежурный смены Петров')throw Error('Invalid finish payload');
  if(!finishBody?.message_id)throw Error('Finish omitted message_id');

  // Принятый доклад появился в телефонограммах карточки.
  await expect(page.locator('#notificationHistory')).toContainText('Дежурный смены Петров');
  await expect(page.locator('#notificationHistory')).toContainText('Служба 101');

  if(errors.length)throw Error(errors.join('\n'));
  console.log(`Briefing browser flow (${process.env.BROWSER_ENGINE||'chromium'}): PASS (mock HTTP, no Backend/model)`);
  console.log(path.join(shots,'briefing-dialog.png'));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
