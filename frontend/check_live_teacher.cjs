// Read-only browser walk through the deployed teacher cabinet.
const {chromium, expect} = require('playwright/test');
const fs = require('node:fs/promises');
const path = require('node:path');

(async()=>{
 const output=process.env.TRAINER_CHECK_OUTPUT || path.resolve(__dirname,'../artifacts/teacher-live-check');
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width:1600,height:900}});
 if(process.env.TRAINER_CHECK_LOCAL_CSS==='1'){
  await page.route('**/assets/cabinet.css',route=>route.fulfill({path:path.resolve(__dirname,'assets/cabinet.css'),contentType:'text/css'}));
 }
 const errors=[];page.on('pageerror',error=>errors.push(error.message));
 try{
  await page.goto('https://127.0.0.1:3000/login');
  await page.locator('[name=username]').fill('prepod');
  await page.locator('[name=password]').fill(process.env.TRAINER_CHECK_PASSWORD);
  await page.locator('button[type=submit]').click();
  await page.waitForURL('**/portal');
  for(const [slug,heading] of [
   ['portal','Кабинет преподавателя'],['assessment','Оценка и статистика'],
   ['tickets','Билеты и задачи'],['scenarios','Библиотека сценариев'],
   ['generation','Черновик сценария и эталона'],['instructor','Настройка оценки задания'],
   ['materials','Справочная библиотека']]){
    await page.goto(`https://127.0.0.1:3000/${slug}`);
    await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible({timeout:15000});
    if(slug==='assessment')await expect(page.locator('#intro')).not.toContainText('Загрузка данных');
    if(slug==='generation')await expect(page.getByText(/при облачном провайдере|передаётся внешнему сервису/i)).toHaveCount(0);
    for(const width of [1600,1280,760]){
     await page.setViewportSize({width,height:900});
     await page.screenshot({path:path.join(output,`${slug}-${width}.png`)});
     if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1)){
      const offenders=await page.evaluate(()=>[...document.querySelectorAll('body *')].filter(el=>el.getBoundingClientRect().right>innerWidth+1).slice(0,8).map(el=>`${el.tagName.toLowerCase()}#${el.id}.${el.className}`));
      throw Error(`${slug}@${width}: horizontal overflow (${offenders.join(', ')})`);
     }
    }
  }
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Teacher cabinet pages: PASS (7 screens × 3 widths, no page errors or overflow)');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1});
