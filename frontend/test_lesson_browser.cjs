// Headless UI regression with intercepted HTTP: never contacts a running server.
const playwright=require('playwright/test'),{expect}=playwright;
// Движок выбирается BROWSER_ENGINE: chromium покрывает Chrome и Яндекс.Браузер,
// firefox — второй движок из требований к совместимости.
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000';
const now=new Date().toISOString();
const scenario={id:'test_case',title:'Учебный пожар',category_id:'fire',enabled:true};
let state='planned',cards=[],lessonExists=true,createdBody=null;
const sipTest=process.env.LESSON_TEST_SIP==='1';let callRequests=0;
function lesson(){return {id:'lesson-test',title:'Практика',state,mode:'fill',members:['student-test'],cards_per_student:null,completed:cards.filter(c=>c.status==='Завершена').length,active_session_id:cards.find(c=>c.status!=='Завершена')?.id||null,exhausted:false,cards};}
function makeCard(){return {id:'card-'+(cards.length+1),number:910001+cards.length,title:scenario.title,scenario_id:scenario.id,lesson_id:'lesson-test',lesson_position:cards.length+1,created_at:now,status:'Новая',revision:0,card:{country:'Россия',city:'Москва',description:'',services:[],classifier_features:[]},events:[],service_states:{},transport:'text',messages:[{role:'assistant',content:'Это учебный вызов.'}],assessment_enabled:false};}
async function serve(page,role){
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  let data;
  if(p.startsWith('/api/')){
   if(p==='/api/v1/auth/me')data={id:role+'-test',role,display_name:'Тест',active:true};
   else if(p==='/api/v1/health')data={status:'ok',provider:'mock'};
   else if(p==='/api/v1/student/classifier')data={groups:[],records:[]};
   else if(p==='/api/v1/student/routing/catalog')data={services:['Служба 101'],flags:[]};
   else if(p==='/api/v1/student/routing/preview')data={rules_version:'full-v2',suggestions:[],excluded:[],unresolved:[],warnings:[]};
   else if(p==='/api/v1/student/assignments')data=[];
   else if(p==='/api/v1/student/lessons')data=[lesson()];
   else if(p==='/api/v1/student/sessions')data=cards;
   else if(p.endsWith('/lesson-test/next')){let c=cards.find(c=>c.status!=='Завершена');if(!c){c=makeCard();if(sipTest){c.transport='sip';c.sip_extension='202';c.messages=[];}cards.push(c);}data=c;}
   else if(p.startsWith('/api/v1/student/sessions/')){
    const parts=p.split('/'),c=cards.find(c=>c.id===parts[5]);if(!c)throw Error('Unknown card '+p);
    if(parts[6]==='call'){if(request.method()==='POST'){callRequests++;c.call_id='call-'+c.id;}return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(request.method()==='POST'?c:{status:'active'})});}
    if(parts[6]==='card'){c.card=request.postDataJSON().card;c.revision++;c.status='В работе';}
    if(parts[6]==='finish'){c.status='Завершена';c.finished_at=now;c.evaluation={status:'not_configured',score_percent:null};}
    data=c;
   }else if(p==='/api/scenarios')data=[scenario,{...scenario,id:'medical_case',title:'Учебная медицина',category_id:'medical',difficulty:'advanced',dds_profile:'medical'}];
   else if(p==='/api/v1/instructor/categories')data=[{id:'fire',title:'Пожары'},{id:'medical',title:'Медицина'}];
   else if(p==='/api/v1/instructor/curriculum')data={difficulties:[{id:'basic',title:'Базовый'},{id:'standard',title:'Стандартный'},{id:'advanced',title:'Повышенный'}],profiles:[{id:'general',title:'Общий профиль 112'},{id:'medical',title:'Скорая помощь'}]};
   else if(p==='/api/v1/instructor/routing/catalog')data={rules_version:'full-v2',services:['Служба 101','ЦОДД'],flags:[]};
   else if(p==='/api/v1/instructor/students')data=[{id:'student-test',display_name:'Студент'}];
   else if(p==='/api/v1/instructor/groups')data=[{id:'group-test',title:'Группа',students:[{id:'student-test',display_name:'Студент'}],member_ids:['student-test']}];
   else if(p==='/api/v1/instructor/assignments')data=[];
   else if(p==='/api/v1/instructor/sessions')data=[];
   else if(p==='/api/v1/instructor/lessons'){
    if(request.method()==='POST'){createdBody=request.postDataJSON();lessonExists=true;state='planned';data=lesson();}else data=lessonExists?[lesson()]:[];
   }else if(p.endsWith('/lesson-test/start')){state='running';data=lesson();}
   else if(p.endsWith('/lesson-test/finish')){state='finished';data=lesson();}
   else if(p.endsWith('/lesson-test/report'))data={id:'lesson-test',title:'Практика',state,summary:{participants:1,issued:cards.length,completed:cards.length},participants:[{student_id:'student-test',display_name:'Студент',completed:cards.length,cards}],events:[]};
   else throw Error('Unexpected API '+p);
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/'?'student.html':p==='/portal'?'portal.html':p.slice(1);
  if(file.includes('..'))throw Error('Unsafe test asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}
(async()=>{
 const browser=await engine.launch({headless:true,...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await serve(page,'student');await page.goto(origin);await page.locator('#activeLesson').selectOption('lesson-test');await page.locator('#joinLesson').click();
  await expect(page.locator('#lessonState')).toContainText('ожидание старта');state='running';
  await expect(page.locator('#cardNumber')).toContainText('910001',{timeout:12000});await page.keyboard.press('Escape');
  await page.locator('#description').fill('Синтетическое сообщение');await page.locator('#finish').click();await page.locator('#confirmFinish').click();
  await expect(page.locator('#cardNumber')).toContainText('910002',{timeout:12000});
  await page.reload();await expect(page.locator('#cardNumber')).toContainText('910002',{timeout:12000});if(cards.length!==2)throw Error('Reload duplicated card');
  if(sipTest&&callRequests!==2)throw Error('SIP must start exactly once per issued card, including reload');
  await page.keyboard.press('Escape');await page.locator('#description').fill('Несохранённый черновик');
  state='finished';cards[1].status='Завершена';cards[1].completed_by={role:'teacher',name:'Преподаватель',reason:'Конец практики'};
  await expect(page.locator('#auditDialog')).toBeVisible({timeout:12000});await expect(page.locator('#auditContent')).toContainText('Несохранённый черновик');
  await page.reload();await expect(page.locator('#lessonState')).toContainText('завершено',{timeout:12000});
  if(errors.length)throw Error(errors.join('\n'));await page.close();
  lessonExists=false;cards=[];const teacher=await browser.newPage();teacher.on('pageerror',e=>errors.push(e.message));teacher.on('dialog',d=>d.accept('Практика завершена'));
  await serve(teacher,'teacher');await teacher.goto(origin+'/portal');await teacher.locator('#lessonTitle').fill('Практика');await teacher.locator('#lessonCategories').selectOption(['fire','medical']);
  await teacher.locator('#lessonDifficulty').selectOption('basic');await teacher.locator('#lessonProfile').selectOption('general');
  await expect(teacher.locator('#lessonScenarios option')).toHaveCount(1);
  // Рабочее место, адресное задание, многозадачность и адаптация задаются в форме.
  await teacher.locator('#lessonMode').selectOption('fill');
  await teacher.locator('#lessonScenarios').selectOption(['test_case']);
  await teacher.locator('#lessonPlaces input[data-place-student-id]').fill('АРМ-7');
  await teacher.locator('#lessonPlaces select[data-target-student-id]').selectOption('test_case');
  await teacher.locator('#lessonParallel').fill('2');await teacher.locator('#lessonAdaptive').check();
  await teacher.locator('#lessonUnlimited').check();await teacher.locator('#lessonForm button').click();
  await expect(teacher.getByRole('button',{name:'Начать',exact:true})).toBeVisible();if(createdBody.cards_per_student!==null||createdBody.category_ids.length!==2)throw Error('Invalid lesson payload');
  if(createdBody.difficulty!=='basic'||createdBody.dds_profile!=='general')throw Error('Curriculum filters not submitted');
  if(createdBody.parallel_cards!==2||createdBody.adaptive_difficulty!==true)throw Error('Multitasking options not submitted');
  if(createdBody.workstations['student-test']!=='АРМ-7')throw Error('Workstation not submitted');
  if(createdBody.student_scenarios['student-test']!=='test_case')throw Error('Addressed task not submitted');
  await teacher.getByRole('button',{name:'Начать',exact:true}).click();await expect(teacher.locator('#lessons')).toContainText('Идёт');
  await teacher.getByRole('button',{name:'Отчёт и участники'}).click();await expect(teacher.locator('#lessonReportContent')).toContainText('Студент');await teacher.locator('#closeLessonReport').click();
  await teacher.getByRole('button',{name:'Завершить всем'}).click();await expect(teacher.locator('#lessons')).toContainText('Завершено');
  if(errors.length)throw Error(errors.join('\n'));console.log('Lesson browser flow: PASS (mock HTTP, student + teacher)');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
