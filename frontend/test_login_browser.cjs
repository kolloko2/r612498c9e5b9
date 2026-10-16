// Login, explicit demo autofill and responsive layout; intercepted HTTP only.
const {chromium,expect}=require('playwright/test');
const fs=require('node:fs/promises'),path=require('node:path');
const origin='http://127.0.0.1:3000',root=__dirname;
const accounts=[
 {role:'admin',username:'demo.admin',password:'example-admin-only',display_name:'Администратор',workstation:''},
 {role:'teacher',username:'demo.teacher',password:'example-teacher-only',display_name:'Преподаватель',workstation:''},
 {role:'student',username:'demo.student1',password:'example-student-only',display_name:'Слушатель 1',workstation:'АРМ-1'},
 {role:'student',username:'demo.student2',password:'example-student-only',display_name:'Слушатель 2',workstation:'АРМ-2'}
];
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  let bootstrap=false,published=true,loginBody=null,secondFactor=false;
  await page.route('**/*',async route=>{
   const request=route.request(),url=new URL(request.url());
   if(url.origin!==origin)return route.abort();
   const p=url.pathname;
   if(p.startsWith('/api/')){
    let data;
    if(p==='/api/v1/auth/status')data={bootstrap_required:bootstrap};
    else if(p==='/api/v1/auth/directory-status')data={configured:false};
    else if(p==='/api/v1/auth/demo-access')data={enabled:published,accounts:published?accounts:[]};
    else if(p==='/api/v1/auth/login'){loginBody=request.postDataJSON();data=secondFactor?{mfa_required:true,mfa_token:'test-ticket'}:{user:{role:'student'}};}
    else throw Error('Unexpected API '+p);
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
   }
   if(p==='/portal')return route.fulfill({contentType:'text/html',body:'<h1>Кабинет</h1>'});
   const file=p==='/login'?'login.html':p.slice(1);
   if(file.includes('..'))throw Error('Unsafe asset');
   return route.fulfill({body:await fs.readFile(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.svg')?'image/svg+xml':'text/html'});
  });
  await page.goto(origin+'/login');
  await expect(page.locator('.demo-account')).toHaveCount(4);
  await expect(page.locator('#demoAccess')).toContainText('example-student-only');
  await page.getByRole('button',{name:'Выбрать: Слушатель 2',exact:true}).click();
  await expect(page.locator('[name=username]')).toHaveValue('demo.student2');
  await expect(page.locator('[name=password]')).toHaveValue('example-student-only');
  await expect(page.locator('[name=workstation]')).toHaveValue('АРМ-2');
  await page.getByRole('button',{name:'Продолжить',exact:true}).click();
  await expect(page).toHaveURL(origin+'/portal');
  expect(loginBody).toEqual({username:'demo.student2',password:'example-student-only'});
  expect(await page.evaluate(()=>localStorage.getItem('workstation'))).toBe('АРМ-2');
  await page.goto(origin+'/login');await expect(page.locator('.demo-account')).toHaveCount(4);
  await fs.mkdir(path.join(root,'../artifacts/final-prep-2026-10-16'),{recursive:true});
  await page.screenshot({path:path.join(root,'../artifacts/final-prep-2026-10-16/login-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:path.join(root,'../artifacts/final-prep-2026-10-16/login-mobile.png'),fullPage:true});
  secondFactor=true;
  await page.getByRole('button',{name:'Выбрать: Преподаватель',exact:true}).click();
  await page.getByRole('button',{name:'Продолжить',exact:true}).click();
  await expect(page.locator('#mfaForm')).toBeVisible();
  await page.getByRole('button',{name:'Выбрать: Слушатель 1',exact:true}).click();
  await expect(page.locator('#mfaForm')).toBeHidden();
  await expect(page.locator('[name=username]')).toHaveValue('demo.student1');
  published=false;await page.reload();await expect(page.locator('#controls')).toBeEnabled();
  await expect(page.locator('#demoAccess')).toBeHidden();
  bootstrap=true;published=true;await page.reload();await expect(page.locator('#heading')).toHaveText('Первый запуск');
  await expect(page.locator('#demoAccess')).toBeHidden();
  expect(errors).toEqual([]);
  console.log('Login browser: PASS (roles, credentials, MFA, bootstrap, mobile layout)');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
