const playwright=require('playwright/test'),{expect}=playwright;
// Движок выбирается BROWSER_ENGINE: chromium покрывает Chrome и Яндекс.Браузер,
// firefox — второй движок из требований к совместимости.
const engine=playwright[process.env.BROWSER_ENGINE||'chromium'];
if(!engine)throw Error('Unknown BROWSER_ENGINE '+process.env.BROWSER_ENGINE);
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin=process.env.LIVE_MAP_URL||'http://127.0.0.1:3000',sid='11111111-1111-4111-8111-111111111111';
async function serve(page,withCoordinates=true,mode='fill'){await page.route('**/*',async route=>{
 const url=new URL(route.request().url()),p=url.pathname;if(url.origin!==origin)throw Error('External map request: '+url.origin);
 const json=body=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 if(p.endsWith('/map/manifest'))return json({title:'Москва / Московская область',addresses:200000});
 if(p.endsWith('/map/features'))return json({features:[{kind:'road',name:'Лесная',coordinates:[[37.61,55.7558],[37.63,55.7558]]}],truncated:false});
 if(p.endsWith('/map/search'))return json({results:[{label:'Химки, Лесная улица, 7',latitude:55.89,longitude:37.44}]});
 if(p===`/api/v1/student/sessions/${sid}`)return json({exercise_mode:mode,status:'В работе',card:{city:'Москва',street:'Лесная',house:'7',...(withCoordinates?{latitude:55.7558,longitude:37.6173}:{})}});
 if(process.env.LIVE_MAP_URL)return route.continue();
 if(p==='/map-data/region.pmtiles'){
  const archive=await fs.open(path.join(root,'../deploy/maps/region.pmtiles'),'r');
  try{const size=(await archive.stat()).size,range=/bytes=(\d+)-(\d+)/.exec(route.request().headers().range||'');
   if(!range)throw Error('PMTiles must use range requests');
   const start=Number(range[1]),end=Math.min(Number(range[2]),size-1),buffer=Buffer.alloc(end-start+1);await archive.read(buffer,0,buffer.length,start);
   return route.fulfill({status:206,body:buffer,headers:{'content-type':'application/octet-stream','content-range':`bytes ${start}-${end}/${size}`,'accept-ranges':'bytes',etag:'"test-archive"'}});
  }finally{await archive.close();}
 }
 const file=p==='/map'?'map.html':decodeURIComponent(p.slice(1));return route.fulfill({body:await fs.readFile(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':file.endsWith('.json')?'application/json':file.endsWith('.png')?'image/png':file.endsWith('.pbf')?'application/octet-stream':'text/html'});
});}
(async()=>{
 const browser=await engine.launch({headless:true,args:['--enable-unsafe-swiftshader'],...(process.env.LESSON_BROWSER_PATH?{executablePath:process.env.LESSON_BROWSER_PATH}:{})});
 try{
 const page=await browser.newPage({viewport:{width:1100,height:850},ignoreHTTPSErrors:true}),errors=[];page.on('pageerror',e=>errors.push(e.message));await serve(page);await page.goto(`${origin}/map?sid=${sid}`);
  await expect(page.locator('#latitude')).toHaveText('55.755800');await expect(page.locator('#mapCoverage')).toContainText('Московская область');await expect(page.locator('#tileNotice')).toBeHidden();
  await page.locator('#zoomIn').click();await expect(page.locator('#zoomLabel')).toHaveText('Масштаб 17');
  await page.locator('#geocodeQuery').fill('Химки Лесная 7');await page.locator('#geocodeForm button').click();await page.getByRole('button',{name:'Химки, Лесная улица, 7',exact:true}).click();await expect(page.locator('#latitude')).toHaveText('55.890000');
  const empty=await browser.newPage({ignoreHTTPSErrors:true});await serve(empty,false);await empty.goto(`${origin}/map?sid=${sid}`);await expect(empty.locator('#mapCanvas')).toBeVisible();await expect(empty.locator('#emptyMap')).toBeHidden();
  await expect(empty.locator('#copyPoint')).toBeDisabled();
  const canvas=page.locator('#mapCanvas'),before=await page.locator('#selectedCoordinates').inputValue();
  await canvas.click({position:{x:130,y:110}});await expect(page.locator('#selectedCoordinates')).not.toHaveValue(before);
  const picked=await page.locator('#selectedCoordinates').inputValue(),box=await canvas.boundingBox();
  await page.mouse.move(box.x+200,box.y+150);await page.mouse.down();await page.mouse.move(box.x+300,box.y+200,{steps:8});await page.mouse.up();
  await expect(page.locator('#selectedCoordinates')).toHaveValue(picked);
  await page.locator('#panMap').click();await canvas.click({position:{x:140,y:100}});await expect(page.locator('#selectedCoordinates')).toHaveValue(picked);
  await page.locator('#selectPoint').click();await canvas.focus();await page.keyboard.press('Enter');await expect(page.locator('#selectedCoordinates')).not.toHaveValue(picked);
  await page.locator('#originalPoint').click();await expect(page.locator('#selectedCoordinates')).toHaveValue('55.755800, 37.617300');
  await expect(page.locator('#usePoint')).toBeEnabled();
  await page.evaluate(()=>{window.receivedPoints=[];window.pointChannel=new BroadcastChannel('trainer112-geocoder');window.pointChannel.onmessage=e=>window.receivedPoints.push(e.data);});
  await page.locator('#usePoint').click();
  await expect.poll(()=>page.evaluate(()=>window.receivedPoints.length)).toBe(1);
  const sent=await page.evaluate(()=>window.receivedPoints[0]);expect(sent.sid).toBe(sid);expect(sent.latitude).toBe(55.7558);expect(sent.longitude).toBe(37.6173);
  await expect(page.locator('#status')).toContainText('Подтвердите');
  const dds=await browser.newPage({viewport:{width:390,height:844},deviceScaleFactor:2,ignoreHTTPSErrors:true});await serve(dds,false,'actions');await dds.goto(`${origin}/map?sid=${sid}`);
  await dds.locator('#mapCanvas').focus();await dds.keyboard.press('Enter');await expect(dds.locator('#selectedCoordinates')).toHaveValue('55.755800, 37.617300');
  await expect(dds.locator('#copyPoint')).toBeEnabled();await expect(dds.locator('#usePoint')).toBeHidden();
  expect(await dds.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await fs.mkdir(path.join(root,'../artifacts/map-point-selection'),{recursive:true});await page.screenshot({path:path.join(root,'../artifacts/map-point-selection/map.png'),fullPage:true});
  await page.evaluate(()=>{map.jumpTo({center:[37.463,55.765],zoom:13});});
  await page.waitForFunction(()=>map.loaded());
  await page.screenshot({path:path.join(root,'../artifacts/map-point-selection/maplibre-river.png'),fullPage:true});
  const broken=await browser.newPage({ignoreHTTPSErrors:true});await serve(broken);
  await broken.route('**/map-data/region.pmtiles',route=>route.fulfill({status:503,body:'Map not installed'}));
  await broken.goto(`${origin}/map?sid=${sid}`);await expect(broken.locator('#retryMap')).toBeVisible();
  await broken.unroute('**/map-data/region.pmtiles');await broken.locator('#retryMap').click();
  await expect(broken.locator('#tileNotice')).toBeHidden({timeout:15000});
  const locked=await browser.newPage({ignoreHTTPSErrors:true});await serve(locked);
  await locked.route(`**/api/v1/student/sessions/${sid}`,route=>route.fulfill({json:{exercise_mode:'fill',status:'Завершена',card:{}}}));
  await locked.goto(`${origin}/map?sid=${sid}`);await expect(locked.locator('#usePoint')).toBeHidden();
  if(errors.length)throw Error(errors.join('\n'));console.log('Regional map browser: PASS');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
