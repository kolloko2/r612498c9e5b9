// Режим новичка: пошаговое объяснение АРМ. Intercepted HTTP only.
const playwright=require('playwright/test'),{expect}=playwright;
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',shots=path.join(root,'..','tmp');

async function serve(page){
 await page.route('**/*',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  if(p.startsWith('/api/')){
   let data={};
   if(p==='/api/v1/auth/me')data={id:'s1',role:'student',display_name:'Диспетчер',active:true};
   else if(p==='/api/v1/health')data={status:'ok',provider:'mock'};
   else if(p==='/api/v1/student/classifier')data={version:'v1',groups:[],records:[]};
   else if(p==='/api/v1/student/routing/catalog')data={rules_version:'v1',services:[],flags:[]};
   else if(p.endsWith('/sessions'))data=[];
   else if(p.endsWith('/assignments')||p.endsWith('/lessons'))data=[];
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/'?'student.html':p.slice(1);
  if(file.includes('..'))throw Error('Unsafe asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}

(async()=>{
 await fs.mkdir(shots,{recursive:true});
 const browser=await engine.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await serve(page);await page.goto(origin);

  // Первый вход: тур запускается сам.
  const box=page.locator('.onboarding-box');
  await expect(box).toBeVisible({timeout:5000});
  await expect(box.locator('.onboarding-step')).toHaveText('Шаг 1 из 11');
  await expect(box.locator('h3')).toHaveText('Список происшествий');
  await expect(box.locator('[data-onboarding="back"]')).toBeDisabled();
  await page.screenshot({path:path.join(shots,'onboarding.png')});

  await box.locator('[data-onboarding="next"]').click();
  await expect(box.locator('.onboarding-step')).toHaveText('Шаг 2 из 11');
  await expect(box.locator('[data-onboarding="back"]')).toBeEnabled();
  // Шаг про адрес обязан предупреждать об ошибке в названии улицы.
  for(let i=2;i<4;i++)await box.locator('[data-onboarding="next"]').click();
  await expect(box.locator('h3')).toContainText('Адрес');
  await expect(box.locator('p')).toContainText('Дубнинская');

  await box.locator('[data-onboarding="stop"]').click();
  await expect(page.locator('.onboarding-box')).toHaveCount(0);

  // Повторный заход тур не навязывает, но кнопка его возвращает.
  await page.reload();
  await page.waitForTimeout(1200);
  await expect(page.locator('.onboarding-box')).toHaveCount(0);
  await page.locator('#onboarding').click();
  await expect(page.locator('.onboarding-box')).toBeVisible();

  // Последний шаг закрывает тур.
  for(let i=0;i<11;i++)await page.locator('[data-onboarding="next"]').click();
  await expect(page.locator('.onboarding-box')).toHaveCount(0);

  if(errors.length)throw Error(errors.join('\n'));
  console.log(`Onboarding browser flow (${process.env.BROWSER_ENGINE||'chromium'}): PASS`);
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
