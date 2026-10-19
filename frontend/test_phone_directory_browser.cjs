// Isolated UI test: real phone controls, mocked contact backend, no external calls.
const {chromium,expect}=require('playwright/test');
const fs=require('node:fs/promises'),path=require('node:path');
const origin='http://127.0.0.1:3000';
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1280,height:800}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());
   if(url.origin!==origin)return route.abort();
   if(url.pathname==='/api/v1/student/softphone')return route.fulfill({json:{enabled:false,reason:'Текстовая проверка'}});
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:`<!doctype html><html lang="ru"><meta charset="utf-8"><link rel="stylesheet" href="/assets/student.css"><body><div class="training-strip"><a href="/portal">Мой кабинет</a></div><script>
    window.calls=[];window.trainerPhone={contacts:async()=>Array.from({length:10},(_,i)=>({id:'c'+i,name:i===0?'Начальник смены':'Контакт '+i,role_title:i===1?'Специалист бригады':'Руководитель организации',number:String(8100+i),available:i!==1,required:i===2})),dial:async number=>{if(number==='9999')throw Error('Номер отсутствует в учебном справочнике этой ДДС');window.calls.push(number);}};
   </script><script src="/assets/softphone.js"></script></body></html>`});
   return route.fulfill({body:await fs.readFile(path.join(__dirname,url.pathname.slice(1))),contentType:url.pathname.endsWith('.css')?'text/css':'text/javascript'});
  });
  await page.goto(origin);
  await page.locator('#softphoneToggle').click();
  await expect(page.locator('.softphone-directory button')).toHaveCount(10);
  await expect(page.locator('.softphone-directory')).toContainText('10/10');
  await expect(page.locator('.softphone-directory button').nth(1)).toBeDisabled();
  await page.getByRole('button',{name:'Цифра 8',exact:true}).click();
  await page.getByRole('button',{name:'Цифра 1',exact:true}).click();
  await expect(page.locator('#softphoneNumber')).toHaveValue('81');
  await page.getByRole('button',{name:'Удалить цифру'}).click();
  await expect(page.locator('#softphoneNumber')).toHaveValue('8');
  await page.locator('#softphoneNumber').fill('9999');await page.locator('#softphoneDial').click();
  await expect(page.locator('.softphone-dial-error')).toContainText('отсутствует');
  await page.locator('.softphone-directory button').first().click();
  await expect(page.locator('#softphoneNumber')).toHaveValue('8100');
  await fs.mkdir(path.join(__dirname,'..','tmp'),{recursive:true});
  await page.screenshot({path:path.join(__dirname,'..','tmp','phone-directory-desktop.png')});
  await page.setViewportSize({width:390,height:780});
  await page.screenshot({path:path.join(__dirname,'..','tmp','phone-directory-mobile.png')});
  const overflow=await page.locator('#softphoneDialog').evaluate(d=>d.scrollWidth>d.clientWidth+2);
  if(overflow)throw Error('Phone dialog has horizontal overflow');
  await page.locator('#softphoneDial').click();
  await expect(page.locator('#softphoneDialog')).not.toBeVisible();
  if((await page.evaluate(()=>window.calls)).join()!=='8100')throw Error('Wrong dialed contact');
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Phone directory browser: PASS (10 contacts, keypad, disabled contact, errors, desktop/mobile)');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
