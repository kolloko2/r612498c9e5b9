const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const root=path.resolve(__dirname,'../..');
const out=path.join(root,'artifacts/documentation-2026-09-27/screens');
fs.mkdirSync(out,{recursive:true});
// Synthetic demo account already exists; do not create or reset any accounts.
const seed=fs.readFileSync(path.join(root,'tools/seed_demo.py'),'utf8');
const password=seed.match(/^PASSWORD = "([^"]+)"/m)[1];
(async()=>{
 const browser=await chromium.launch({headless:true});
 const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:1440,height:960}});
 const page=await context.newPage();
 const snap=async(name,selector)=>{
   if(selector) await page.locator(selector).scrollIntoViewIfNeeded();
   await page.screenshot({path:path.join(out,name+'.png')});
   console.log(name);
 };
 await page.goto('https://127.0.0.1:3000/login');
 await snap('01_login');
 for(const user of ['prepod','kursant1']){
   await context.clearCookies();
   await page.goto('https://127.0.0.1:3000/login');
   await page.locator('[name=username]').fill(user);
   await page.locator('[name=password]').fill(password);
   await page.locator('button[type=submit]').click();
   await page.waitForURL('**/portal');
   if(user==='prepod'){
     await page.locator('#teacherPanel').waitFor({state:'visible'});
     await page.locator('#assignmentGroup option').first().waitFor({state:'attached'});
     await page.waitForTimeout(2500);
     await snap('02_teacher');
     await page.locator('#lessonMode').selectOption('actions');
     await page.locator('#lessonMode').dispatchEvent('change');
     await snap('03_lesson','#lessonForm');
     for(const slug of ['scenarios','generation','materials','assessment','tickets']){
       await page.goto('https://127.0.0.1:3000/'+slug);
       await page.locator('h1').first().waitFor();
       await page.waitForTimeout(1200);
       await snap('teacher_'+slug);
     }
   }else{
     await page.locator('#studentPanel').waitFor({state:'visible'});
     await snap('04_student');
     const evidence=JSON.parse(fs.readFileSync(path.join(root,'artifacts/documentation-2026-09-27/session.json'),'utf8'));
     await page.evaluate(sid=>localStorage.setItem('studentSession',sid),evidence.session_id);
     await page.goto('https://127.0.0.1:3000/');
     await page.addLocatorHandler(page.locator('.onboarding'),async()=>page.locator('[data-onboarding="stop"]').click());
     const detail=await (await page.request.get('https://127.0.0.1:3000/api/v1/student/sessions/'+evidence.session_id)).json();
     await page.getByRole('button',{name:'Открыть карточку '+detail.number,exact:true}).click();
     await page.locator('#cardPanel').waitFor({state:'visible'});
     await snap('05_arm');
     const own=page.locator('.service-tile.own-service');
     if(!await own.evaluate(el=>el.open))await own.locator('summary').click();
     await snap('06_services');
     await own.locator('.service-edit').click();
     await snap('06_status');
     await page.locator('#closeResponsePanel').click();
     await page.locator('#cardResponseTools').click();
     await snap('07_tools');
     await page.locator('#closeResponsePanel').click();
     await page.locator('#audit').click();
     await page.locator('#auditDialog').waitFor({state:'visible'});
     await snap('08_report');
     await page.goto('https://127.0.0.1:3000/map?sid='+evidence.session_id);
     await page.locator('#geocodeQuery').fill('Москва Олонецкий 4');
     await page.locator('#geocodeForm button').click();
     await page.locator('#geocodeResults button').first().waitFor({timeout:30000});
     await page.locator('#geocodeResults button').first().click();
     await page.waitForTimeout(2500);
     await snap('09_map');
   }
 }
 await browser.close();
})().catch(e=>{console.error(e.message);process.exitCode=1});
