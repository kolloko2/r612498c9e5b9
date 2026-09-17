const playwright=require('playwright/test'),{expect}=playwright;
// Движок выбирается BROWSER_ENGINE: chromium покрывает Chrome и Яндекс.Браузер,
// firefox — второй движок из требований к совместимости.
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',sid='11111111-1111-4111-8111-111111111111';
async function serve(page,withCoordinates=true){await page.route('**/*',async route=>{
 const url=new URL(route.request().url()),p=url.pathname;if(url.origin!==origin)return route.abort();
 const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 if(p.endsWith('/map/manifest'))return json({title:'Москва / Московская область',addresses:200000});
 if(p.endsWith('/map/features'))return json({features:[{kind:'road',name:'Лесная',coordinates:[[37.61,55.7558],[37.63,55.7558]]}],truncated:false});
 if(p.endsWith('/map/search'))return json({results:[{label:'Химки, Лесная улица, 7',latitude:55.89,longitude:37.44}]});
 if(p===`/api/v1/student/sessions/${sid}`)return json({card:{city:'Москва',street:'Лесная',house:'7',...(withCoordinates?{latitude:55.7558,longitude:37.6173}:{})}});
 const file=p==='/map'?'map.html':p.slice(1);return route.fulfill({body:await fs.readFile(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
});}
(async()=>{
 const browser=await engine.launch({headless:true,...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1100,height:850}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await serve(page);await page.goto(`${origin}/map?sid=${sid}`);
  await expect(page.locator('#latitude')).toHaveText('55.755800');await expect(page.locator('#mapCoverage')).toContainText('Московская область');await expect(page.locator('#tileNotice')).toBeHidden();
  await page.locator('#zoomIn').click();await expect(page.locator('#zoomLabel')).toHaveText('Масштаб 17');
  await page.locator('#geocodeQuery').fill('Химки Лесная 7');await page.locator('#geocodeForm button').click();await page.getByRole('button',{name:'Химки, Лесная улица, 7',exact:true}).click();await expect(page.locator('#latitude')).toHaveText('55.890000');
  const empty=await browser.newPage();await serve(empty,false);await empty.goto(`${origin}/map?sid=${sid}`);await expect(empty.locator('#mapCanvas')).toBeVisible();await expect(empty.locator('#emptyMap')).toBeHidden();
  if(errors.length)throw Error(errors.join('\n'));console.log('Regional map browser: PASS');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
