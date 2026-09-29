// Доклад дежурному службы в АРМ: intercepted HTTP only, без Backend и модели.
// Движок выбирается BROWSER_ENGINE (chromium по умолчанию, firefox поддержан).
const playwright=require('playwright/test'),{expect}=playwright;
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',shots=path.join(root,'..','tmp');
const now=process.env.ARM_DDS_PREVIEW?new Date().toISOString():'2026-09-16T09:00:00+00:00';

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

if(process.env.ARM_DDS_PREVIEW){
 Object.assign(card,{exercise_mode:'actions',owner_service:'Служба 101',card_locked:false,crew_options:[],situation_updates:[],practice_with_hints:true,
  practice_hint:{title:'Проверьте карточку',text:'Примите решение по карточке своей службы.',target:'responseStatus'}});
 card.card.services.push('Служба 102','Служба 104','Деп. ЖКХ','ЦЭМП','ЦОДД','Мос.Без.','Мослифт','ОАТИ','ДДС района','ДДС округа');
 for(const name of card.card.services)card.service_states[name]={status:'Добавлена',added_at:now};
 card.allowed_service_statuses={'Служба 101':['Принята','Не принята']};
}
if(process.env.INBOX_REPORT_TEST)Object.assign(card,{exercise_mode:'actions',owner_service:'Служба 101',
 text_input_allowed:false,pending_phone_reports:[],situation_updates:[]});
if(process.env.LESSON_RESTART_TEST)Object.assign(card,{exercise_mode:'actions',owner_service:'Служба 101',
 lesson_id:'lesson-test',practice_with_hints:false});

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
   else if(p==='/api/v1/student/softphone')data={enabled:false,reason:'Номер не назначен'};
   else if(p==='/api/v1/health')data={status:'ok',provider:'mock'};
   else if(p==='/api/v1/student/classifier')data={version:'test-v1',groups:[],records:[]};
   else if(p==='/api/v1/student/routing/catalog')data={rules_version:'full-v2',services:['Служба 101'],flags:[]};
   else if(p==='/api/v1/student/assignments')data=[];
   else if(p==='/api/v1/student/lessons')data=process.env.LESSON_RESTART_TEST?[{id:'lesson-test',title:'Тестовая карточка ДДС',
    state:'running',cards_per_student:1,completed:0,mode:'actions',transport:'sip',sip_extension:'201',
    practice_with_hints:false,restart_session_id:card.id,active_session_id:card.id,active_session_ids:[card.id]}]:[];
   else if(p==='/api/v1/student/sessions')data=[JSON.parse(JSON.stringify(card))];
   else if((process.env.ATTEMPT_RESTART_TEST||process.env.LESSON_RESTART_TEST)&&p===`/api/v1/student/sessions/${card.id}/restart`){
    const old=card.id;Object.assign(card,{id:'fresh-attempt',restarted_from:old,attempt_number:2,practice_with_hints:false,events:[],notifications:[]});data=JSON.parse(JSON.stringify(card));status=201;
   }
   else if(p===`/api/v1/student/sessions/${card.id}/briefings`&&request.method()==='POST'){
    const body=request.postDataJSON();
    briefing={id:'brief-1',service:body.service,state:'open',voice:'baya',simulated:true,recipient_hint:'Начальник дежурной смены',
     messages:[{role:'assistant',content:`Начальник дежурной смены, ${body.service}. Слушаю вас.`,at:now}],report:null};
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
   else if(p===`/api/v1/student/sessions/${card.id}`||p===`/api/v1/student/sessions/${card.id}/open`)data=JSON.parse(JSON.stringify(card));
   else if(p==='/api/v1/student/inbox/poll')data=[];
   else throw Error('Unexpected API '+request.method()+' '+p);
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/'?'student.html':p==='/map'?'map.html':p.slice(1);
  if(file.includes('..'))throw Error('Unsafe test asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}

(async()=>{
 await fs.mkdir(shots,{recursive:true});
 const browser=await engine.launch({headless:true,...(process.env.BROWSER_PATH?{executablePath:process.env.BROWSER_PATH}:{}),args:['--no-sandbox']});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
  await serve(page);await page.goto(origin);

  await page.locator('tr[aria-label="Происшествие 910301"]').click();
  await expect(page.locator('#cardNumber')).toContainText('910301');
  if(process.env.LESSON_RESTART_TEST){
   await expect(page.locator('#restartLessonAttempt')).toBeVisible({timeout:7000});
   await expect(page.locator('#restartLessonAttempt')).toHaveText('Начать карточку заново');
   await page.locator('#restartLessonAttempt').click();
   await expect.poll(()=>page.evaluate(()=>current.id)).toBe('fresh-attempt');
   await expect(page.locator('#restartLessonAttempt')).toBeVisible();
   // В занятии — только красная «Выйти», после выхода — только «Войти».
   await page.locator('#closeCard').click();
   await expect(page.locator('#leaveLesson')).toBeVisible();await expect(page.locator('#joinLesson')).toBeHidden();
   await expect(page.locator('#lessonBadge')).toContainText('Вы в занятии');
   await page.locator('#leaveLesson').click();
   await expect(page.locator('#joinLesson')).toBeVisible();await expect(page.locator('#leaveLesson')).toBeHidden();
   await expect(page.locator('#lessonBadge')).toBeHidden();
   // Обучение уже показывалось — кнопка маленькая.
   await page.evaluate(()=>localStorage.setItem('onboardingDone','1'));await page.reload();
   await expect(page.locator('#startTraining')).toHaveText('Обучение');
   if(errors.length)throw Error(errors.join('\n'));
   console.log('Visible one-card lesson restart: PASS');return;
  }
  if(process.env.INBOX_REPORT_TEST){
   await expect(page.locator('#situationFeed')).toBeHidden();
   card.pending_phone_reports=[{id:'arrived',call_id:'test-call'}];
   // Доклад виден вверху карточки, подтверждать его вручную не нужно.
   await expect(page.locator('#phoneReportBanner')).toContainText('внесён в карточку автоматически',{timeout:12000});
   await expect(page.locator('#phoneReportBanner')).not.toContainText('Подтвердить');
   const banner=await page.locator('#phoneReportBanner').boundingBox(),footer=await page.locator('.card-footer').boundingBox();
   if(!(banner.y+banner.height<footer.y-80))throw Error('Плашка доклада перекрыта подвалом со службами');
   if(errors.length)throw Error(errors.join('\n'));
   console.log('Live phone-report poll without reload: PASS');return;
  }
  if(process.env.VOICE_REPORT_TEST){
   await page.evaluate(()=>{
    Object.assign(current,{exercise_mode:'actions',owner_service:'Служба 101',text_input_allowed:false,
     situation_updates:[{id:'arrived',at:'2026-09-16T09:03:00Z',source:'Старший бригады',
      text:'Секретная учебная вводная',unlocks_status:'Прибытие'}],
     events:[{type:'situation.update',at:'2026-09-16T09:03:00Z',detail:{source:'Старший бригады',text:'Секретная учебная вводная'}}]});
    renderSituationUpdates();
   });
   await expect(page.locator('#latestReport')).toContainText('Отразите услышанное');
   await expect(page.locator('#latestReport')).not.toContainText('Прибытие');
   await expect(page.locator('#situationFeed')).not.toContainText('Секретная учебная вводная');
   await expect(page.locator('#latestReport')).not.toContainText('Доклад с места');
   await page.evaluate(()=>{current.text_input_allowed=true;renderSituationUpdates();});
   await expect(page.locator('#latestReport')).toContainText('Секретная учебная вводная');
   if(errors.length)throw Error(errors.join('\n'));
   console.log('Voice-only report display: PASS');return;
  }
  if(process.env.ATTEMPT_RESTART_TEST){
   await page.evaluate(()=>{current.exercise_mode='actions';current.owner_service='Служба 101';current.scenario_id='dds-guided-practice-v1';current.practice_with_hints=false;renderCard();window.startDdsCoach();});
   await expect(page.locator('#cardPractice')).toBeHidden();await expect(page.locator('#ddsCoach')).toBeHidden();
   await page.evaluate(()=>{current.practice_with_hints=true;current.practice_hint={title:'Проверенный шаг',text:'Проверьте карточку',target:'responseStatus'};renderCard();});
   await page.locator('#cardPractice').click();await expect(page.locator('#ddsCoach')).toBeVisible();
   await page.evaluate(()=>{current.practice_with_hints=false;renderCard();});await expect(page.locator('#ddsCoach')).toBeHidden();
   // Повтор — только в верхней учебной полосе, не в «Реагировании».
   await expect(page.locator('#responseSection #restartAttempt')).toHaveCount(0);
   await expect(page.locator('#restartLessonAttempt')).toHaveText('Начать карточку заново');
   await page.locator('#restartLessonAttempt').click();
   await expect.poll(()=>page.evaluate(()=>current.id)).toBe('fresh-attempt');
   if(errors.length)throw Error(errors.join('\n'));console.log('Practice authorization and restart UI: PASS');return;
  }
  if(process.env.ARM_DDS_PREVIEW){
   await page.locator('#cardTutorial').click();
   await expect(page.locator('.onboarding-box')).toBeVisible();
   await page.locator('[data-onboarding="stop"]').click();
   await page.locator('#cardPractice').click();
   await expect(page.locator('#ddsCoach')).toContainText('Примите решение');
   await page.getByRole('button',{name:'Показать, куда нажать'}).click();
   await expect(page.locator('#responseStatus')).toBeVisible();
   await expect(page.locator('#responseStatus')).toHaveClass(/dds-coach-target/);
   await page.getByRole('button',{name:'Скрыть подсказки'}).click();
   await page.locator('#closeResponsePanel').click();
   await page.locator('#finish').click();
   await expect(page.locator('#finishDialog')).toBeVisible();
   await expect(page.locator('#finishChecklist')).toContainText('Прослушанный доклад не заменяет');
   await page.locator('#finishDialog [data-close]').first().click();
   for(const [width,height] of [[1920,1080],[1280,720],[760,900]]){
    await page.setViewportSize({width,height});
    await expect(page.locator('.dds-mode')).toBeVisible();
    await expect(page.locator('#services .service-status').first()).toContainText('Добавлена');
    await expect(page.locator('.service-edit')).toHaveCount(1);
    if(width>1150){
     await expect(page.locator('#description')).not.toBeEditable();
     const metrics=await page.evaluate(()=>{
      const box=selector=>document.querySelector(selector).getBoundingClientRect();
      const tiles=[...document.querySelectorAll('#services .service-tile')].map(tile=>tile.getBoundingClientRect());
      return {connection:box('.connection'),summary:box('.card-ident'),actions:box('.card-ident-actions'),footer:box('.card-footer'),description:box('.description-block'),upperTileY:Math.min(...tiles.map(tile=>tile.y))};
     });
     if(width===1920&&(Math.abs(metrics.connection.width-280)>2||Math.abs(metrics.summary.width-200)>2||metrics.actions.x<=metrics.summary.right))throw Error('DDS header columns differ from reference proportions');
     if(metrics.footer.height>70||metrics.description.bottom>metrics.upperTileY+1)throw Error('DDS service overflow obscures the card '+JSON.stringify({width,metrics}));
    }
    await page.locator('.service-edit').click();
    await expect(page.locator('#responseSection')).toBeVisible();
    await expect(page.locator('#responseComment')).toBeVisible();
    await expect(page.locator('#addResponse')).toBeEnabled();
    if(width>760){
     await expect.poll(async()=>{
      const panel=await page.locator('#responseSection').boundingBox(),footer=await page.locator('.card-footer').boundingBox();
      return panel.y>=0&&panel.y+panel.height<=footer.y;
     }).toBe(true);
    }
    if(width===1920||width===1280)await page.screenshot({path:path.join(shots,`dds-response-${width}.png`)});
    await page.locator('#closeResponsePanel').click();
    if(width>1150){
     const own=page.locator('#services .own-service');
     await own.locator('summary').click();
     await expect(own).toHaveAttribute('open','');
     const popup=await page.evaluate(()=>{const tile=document.querySelector('#services .own-service').getBoundingClientRect(),history=document.querySelector('#services .own-service .service-tile-history').getBoundingClientRect();return{tile,history};});
     if(Math.abs(popup.tile.x-popup.history.x)>1||popup.history.bottom>popup.tile.top+1)throw Error('DDS history is not anchored above its service');
     if(width===1920)await page.screenshot({path:path.join(shots,'dds-history-1920.png')});
     await own.locator('summary').click();
    }
    if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Horizontal page overflow at '+width);
    if(width===760){
     const header=await page.evaluate(()=>{
      const box=selector=>document.querySelector(selector).getBoundingClientRect();
      return {date:box('.console-meta>div:first-child'),clock:box('#clock'),create:box('#create'),meta:box('.console-meta'),nav:box('nav'),searchHidden:getComputedStyle(document.querySelector('.search-block')).display==='none'};
     });
     if(!header.searchHidden||header.date.right>header.clock.left||header.clock.right>header.create.left||header.nav.top<header.meta.bottom-1)throw Error('Narrow DDS toolbar overlaps');
    }
    await page.evaluate(()=>scrollTo(0,0));
    await page.screenshot({path:path.join(shots,`dds-reference-${width}.png`)});
   }
   if(errors.length)throw Error(errors.join('\n'));
   console.log('DDS layout: PASS at 1920, 1280 and 760; screenshots in tmp');
   return;
  }
  await page.locator('#openBriefing').click();
  await expect(page.locator('#briefingDialog')).toBeVisible();
  // Службы карточки предложены для вызова.
  await expect(page.locator('#briefingService option')).toHaveCount(2);

  await page.locator('#startBriefing').click();
  await expect(page.locator('#briefingTranscript')).toContainText('Начальник дежурной смены, Служба 101. Слушаю вас.');
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
  await expect(page.locator('#briefingRecipient')).toHaveValue('Начальник дежурной смены');
  await page.screenshot({path:path.join(shots,'briefing-dialog.png')});

  await page.locator('#briefingRecipient').fill('Дежурный смены Петров');
  await page.locator('#finishBriefing').click();
  await expect(page.locator('#briefingMissing')).toContainText('Доклад принят');
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
