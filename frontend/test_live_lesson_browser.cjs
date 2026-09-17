// Headless teacher live-dashboard regression. HTTP is intercepted; no service is contacted.
const playwright=require('playwright/test'),{expect}=playwright;
// Движок выбирается BROWSER_ENGINE: chromium покрывает Chrome и Яндекс.Браузер,
// firefox — второй движок из требований к совместимости.
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',now=new Date().toISOString();
let createdBody=null,sessionReads=0;
const members=[{id:'student-1',display_name:'Анна',username:'anna'},{id:'student-2',display_name:'Борис',username:'boris'}];
const lesson={id:'lesson-1',title:'Смена 1',state:'running',mode:'fill',transport:'sip',cards_per_student:3,cards:[]};
function session(){return {id:'session-1',number:42,status:'В работе',created_at:now,student_id:'student-1',assignment_id:'assignment-1',card:{description:'Сохранённая карточка'},messages:[{role:'assistant',content:sessionReads>0?'Новое сохранённое сообщение':'Первое сообщение'}],events:[],teacher_feedback:[]};}
async function serve(page){
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;if(url.origin!==origin)return route.abort();
  if(p.startsWith('/api/')){let data;
   if(p==='/api/v1/auth/me')data={id:'teacher-1',role:'teacher',display_name:'Преподаватель'};
   else if(p==='/api/v1/instructor/routing/catalog')data={rules_version:'full-v2',services:['Служба 101','ЦОДД'],flags:[]};
   else if(p==='/api/v1/instructor/students')data=members;
   else if(p==='/api/v1/instructor/groups')data=[{id:'group-1',title:'Основная',students:members,member_ids:members.map(x=>x.id)},{id:'group-2',title:'Резерв',students:[members[0]],member_ids:['student-1']}];
   else if(p==='/api/scenarios')data=[{id:'scenario-1',title:'Учебный сценарий',enabled:true,category_id:'other'}];
   else if(p==='/api/v1/instructor/curriculum')data={difficulties:[{id:'basic',title:'Базовый'}],profiles:[{id:'general',title:'Общий'}]};
   else if(p==='/api/v1/instructor/categories')data=[{id:'other',title:'Другое'}];
   else if(p==='/api/v1/instructor/assignments')data=[];
   else if(p==='/api/v1/instructor/sessions')data=[session()];
   else if(p==='/api/v1/instructor/sessions/session-1'){data=session();sessionReads++;}
   else if(p==='/api/v1/instructor/lessons'){
    if(request.method()==='POST'){createdBody=request.postDataJSON();data={...lesson,id:'lesson-created'};}else data=[lesson];
   }else if(p==='/api/v1/instructor/lessons/lesson-1/live')data={id:'lesson-1',title:'Смена 1',state:'running',observed_at:now,participants:[{student_id:'student-1',display_name:'Анна',completed:1,active_card:{id:'session-1',number:42,title:'Учебный сценарий',created_at:now,elapsed_seconds:125,transport:'sip',call_id:'call-1',last_event:{type:'service.updated',at:now,detail:'101 приняла'},provider_error:'Учебная ошибка провайдера'},latest_result:{score_percent:80,policy_result:{passed:true}}},{student_id:'student-2',display_name:'Борис',completed:0,active_card:null,latest_result:null}]};
   else throw Error('Unexpected API '+p);
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/portal'?'portal.html':p.slice(1);if(file.includes('..'))throw Error('Unsafe asset');const body=await fs.readFile(path.join(root,file));return route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}
(async()=>{
 const browser=await engine.launch({headless:true,...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];page.on('pageerror',error=>errors.push(error.message));await serve(page);await page.goto(origin+'/portal');
  await page.locator('#lessonTransport').selectOption('sip');const extensionInputs=page.locator('#lessonExtensions input');await expect(extensionInputs).toHaveCount(2);await extensionInputs.nth(0).fill('201');await extensionInputs.nth(1).fill('202');
  await page.locator('#lessonGroup').selectOption('group-2');await page.locator('#lessonGroup').selectOption('group-1');await expect(extensionInputs.nth(0)).toHaveValue('201');await expect(extensionInputs.nth(1)).toHaveValue('202');
  await page.locator('#lessonTitle').fill('SIP-практика');await page.locator('#lessonMode').selectOption('fill');await page.locator('#lessonScenarios').selectOption('scenario-1');await page.locator('#lessonForm button').click();await expect.poll(()=>createdBody).not.toBeNull();if(createdBody.transport!=='sip'||createdBody.sip_extensions['student-1']!=='201'||createdBody.sip_extensions['student-2']!=='202')throw Error('SIP payload is incomplete');
  await page.getByRole('button',{name:'Участники в реальном времени'}).first().click();await expect(page.locator('#liveLessonContent')).toContainText('2 мин 05 сек');await expect(page.locator('#liveLessonContent')).toContainText('service.updated');await expect(page.locator('#liveLessonContent')).toContainText('Учебная ошибка провайдера');await expect(page.locator('#liveLessonContent')).toContainText('80%');await page.locator('#liveLessonDialog').getByRole('button',{name:'Закрыть'}).click();
  await page.locator('#sessions').getByRole('button',{name:'Открыть'}).click();const stop=page.getByLabel('Причина завершения'),feedback=page.getByLabel('Замечание студенту');await stop.fill('Не терять эту причину');await feedback.fill('Не терять эту подсказку');await feedback.focus();await expect(page.locator('#sessionDetail')).toContainText('Новое сохранённое сообщение',{timeout:7000});await expect(stop).toHaveValue('Не терять эту причину');await expect(feedback).toHaveValue('Не терять эту подсказку');if(!(await feedback.evaluate(item=>item===document.activeElement)))throw Error('Focused feedback field was replaced by polling');
  if(errors.length)throw Error(errors.join('\n'));console.log('Live lesson browser flow: PASS (SIP setup, live state, safe session refresh)');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
