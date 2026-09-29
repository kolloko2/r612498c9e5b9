const {chromium,expect}=require('playwright/test');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({args:['--no-sandbox']});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let saved;
  const scenario={id:'practice_test',title:'Учебная практика',enabled:true,editable:true,version:'a'.repeat(64),victim_name:'Заявитель',emotion:'Спокойно',incident:'Повреждение трубы',location:'Учебная 12',known_facts:['Труба повреждена'],unknown_facts:[],opening:'Вода во дворе',practice_plan:[],prefilled_card:{},updates:[],crew_options:[]};
  await page.route('http://practice.test/**',async route=>{
   const req=route.request(),p=new URL(req.url()).pathname;
   let data;
   if(p==='/api/scenarios/practice-draft')data={steps:[{phase:'accept',update_id:'',title:'Проверьте карточку',text:'Проверьте свою службу',sample:'',target:'responseStatus'}]};
   else if(p==='/api/scenarios')data=[scenario];
   else if(p==='/api/scenarios/practice_test'){
    if(req.method()==='PUT'){saved=req.postDataJSON();data={...scenario,...saved,practice_approved_version:'b'.repeat(64)};}else data=scenario;
   }else if(p.startsWith('/api/'))data=[];
   if(data!==undefined)return route.fulfill({json:data});
   const file=path.join(__dirname,p==='/'?'scenarios.html':p);
   try{return route.fulfill({body:await fs.readFile(file),contentType:p.endsWith('.js')?'application/javascript':p.endsWith('.css')?'text/css':'text/html'});}catch{return route.abort();}
  });
  await page.goto('http://practice.test/');
  await page.locator('#selected').selectOption('practice_test');
  await expect(page.locator('#practiceDraft')).toBeEnabled();
  await page.locator('#practiceDraft').click();
  await expect(page.locator('#practiceSteps textarea')).toHaveCount(3);
  await page.locator('#practiceSteps textarea').nth(1).fill('Проверенный преподавателем текст');
  page.on('dialog',d=>d.accept());
  await page.locator('#practiceApprove').click();
  await expect(page.locator('#practiceState')).toHaveText('Подсказки утверждены.');
  assert.equal(saved.practice_confirm,true);
  assert.equal(saved.practice_plan[0].text,'Проверенный преподавателем текст');
  await page.locator('[data-key=title]').fill('Изменённое задание');
  await expect(page.locator('#practiceState')).toContainText('заново');
  assert.deepEqual(errors,[]);
  console.log('PASS practice editor: draft, edit, approval and invalidation notice');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
