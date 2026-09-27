// Headless UI regression with intercepted HTTP: never contacts Backend, a database, or an LLM.
const playwright=require('playwright/test'),{expect}=playwright;
// Движок выбирается BROWSER_ENGINE: chromium покрывает Chrome и Яндекс.Браузер,
// firefox — второй движок из требований к совместимости.
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000';
const now=()=>new Date().toISOString();
const drafts=[];
let failNextRevision=true;
function scenario(){return {id:'ai_test_scenario',title:'Учебное задымление',category_id:'fire',description:'Синтетическая учебная ситуация',victim_name:'Учебный заявитель',incident:'В учебном здании виден дым',location:'Вымышленный город, Учебная улица, дом 10',known_facts:['Дым виден на втором этаже','Заявитель находится снаружи'],unknown_facts:['Причина задымления неизвестна'],emotion:'Обеспокоен',behavior:'Сообщает только известные факты',opening:'В учебном здании виден дым, помогите.',enabled:false};}
function rubric(){return {title:'Эталон учебного задымления',time_limit_seconds:120,criteria:[{id:'street',label:'Название улицы',field:'street',mode:'equals',expected:['Учебная улица'],weight:2},{id:'incident',label:'Описание дыма',field:'description',mode:'contains_all',expected:['виден дым'],weight:3}]};}
function makeDraft(body){const at=now();return {id:body.request_id,status:'draft',revision:1,brief:body.brief,category_id:body.category_id,scenario:scenario(),rubric:rubric(),provider:'mock',model:'mock',history:[{revision:1,comment:'',at,scenario:scenario(),rubric:rubric()}],updated_at:at};}
function summary(d){return {id:d.id,status:d.status,revision:d.revision,title:d.scenario.title,updated_at:d.updated_at};}
async function serve(page){
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  if(p.startsWith('/api/')){
   let data,status=200;
   if(p==='/api/v1/instructor/corrections'&&request.method()==='GET')data=[];
   else if(p==='/api/v1/instructor/generations'&&request.method()==='GET')data=drafts.map(summary);
   else if(p==='/api/v1/instructor/generations'&&request.method()==='POST'){
    const body=request.postDataJSON();let draft=drafts.find(d=>d.id===body.request_id);
    if(!draft){draft=makeDraft(body);drafts.unshift(draft);status=201;}data=draft;
   }else{
    const match=p.match(/^\/api\/v1\/instructor\/generations\/([^/]+)(?:\/(revise|approve))?$/);
    if(!match)throw Error('Unexpected API '+p);
    const draft=drafts.find(d=>d.id===decodeURIComponent(match[1]));if(!draft)return route.fulfill({status:404,contentType:'application/json',body:JSON.stringify({detail:'Черновик не найден'})});
    if(match[2]==='revise'){
     const body=request.postDataJSON();
     if(failNextRevision){failNextRevision=false;return route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({detail:'ИИ не вернул корректный черновик'})});}
     draft.revision++;draft.updated_at=now();draft.scenario={...draft.scenario,title:'Уточнённое учебное задымление'};draft.history.push({revision:draft.revision,comment:body.comment,at:draft.updated_at,scenario:draft.scenario,rubric:draft.rubric});data=draft;
    }else if(match[2]==='approve'){
     const body=request.postDataJSON();if(body.revision!==draft.revision)throw Error('Approval used stale revision');draft.status='approved';draft.approved_scenario_id=draft.scenario.id;draft.updated_at=now();data=draft;
    }else data=draft;
   }
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/generation'?'generation.html':p.slice(1);
  if(file.includes('..'))throw Error('Unsafe test asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}
(async()=>{
 const browser=await engine.launch({headless:true,...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',dialog=>dialog.accept());
  await serve(page);await page.goto(origin+'/generation');
  await expect(page.getByText(/при облачном провайдере|передаётся внешнему сервису/i)).toHaveCount(0);
  await expect(page.locator('#drafts')).toContainText('пока нет');
  await page.locator('#brief').fill('Создать синтетическое занятие о задымлении');await page.locator('#category').selectOption('fire');await page.locator('#ownerService').fill('Служба 101');await page.locator('#generate').click();
  await expect(page.locator('#preview')).toBeVisible();await expect(page.locator('#scenario')).toContainText('Учебная улица');await expect(page.locator('#rubric')).toContainText('Название улицы');await expect(page.locator('#provider')).toContainText('Mock-режим: модель не вызывалась');
  const originalTitle=await page.locator('#previewTitle').textContent();await page.locator('#comment').fill('Уточнить название, не меняя факты');await page.locator('#revise').click();
  await expect(page.locator('#status')).toContainText('ИИ не вернул');await expect(page.locator('#comment')).toHaveValue('Уточнить название, не меняя факты');await expect(page.locator('#previewTitle')).toHaveText(originalTitle);
  await page.locator('#revise').click();await expect(page.locator('#scenario')).toContainText('Уточнённое учебное задымление');await expect(page.locator('#meta')).toContainText('Версия: 2');
  await page.reload();await expect(page.locator('.draft')).toHaveCount(1);await expect(page.locator('#preview')).toBeHidden();await page.locator('.draft').click();await expect(page.locator('#meta')).toContainText('Версия: 2');await expect(page.locator('#history')).toContainText('Уточнить название');
  await page.locator('#approvalCheck').check();await expect(page.locator('#approve')).toBeEnabled();await page.locator('#approve').click();await expect(page.locator('#previewTitle')).toHaveText('Одобрено');await expect(page.locator('#drafts')).toContainText('Одобрено');
  if(errors.length)throw Error(errors.join('\n'));console.log('Generation browser flow: PASS (mock HTTP, no auth/database/LLM)');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
