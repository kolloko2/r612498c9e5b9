// Real deployed pages; credentials supplied by the launching environment only.
const {chromium,expect}=require('playwright/test');
const fs=require('node:fs/promises'),path=require('node:path');
(async()=>{
 const out=process.env.DDS_ACCEPTANCE_OUT || path.resolve(__dirname,'../artifacts/dds-sip-acceptance-2026-09-26');
 const live=JSON.parse(await fs.readFile(path.join(out,'live.json'),'utf8'));
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({ignoreHTTPSErrors:true,viewport:{width:1920,height:1080}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addLocatorHandler(page.locator('.onboarding'),async()=>{
   await page.locator('[data-onboarding="stop"]').click();
  });
  await page.goto('https://127.0.0.1:3000/login');
  await page.locator('[name=username]').fill('kursant1');
  await page.locator('[name=password]').fill(process.env.TRAINER_CHECK_PASSWORD);
  await page.locator('button[type=submit]').click();
  await page.waitForURL('**/portal');
  await page.evaluate(sid=>localStorage.setItem('studentSession',sid),live.session_id);
  await page.goto('https://127.0.0.1:3000/');
  const detail=await (await page.request.get('https://127.0.0.1:3000/api/v1/student/sessions/'+live.session_id)).json();
  await page.getByRole('button',{name:'Открыть карточку '+detail.number,exact:true}).click();
  await expect(page.locator('#cardPanel')).toBeVisible({timeout:15000});
  for(const [w,h] of [[1920,1080],[1280,720],[760,900]]){
   await page.setViewportSize({width:w,height:h});
   await page.screenshot({path:path.join(out,`arm-${w}.png`)});
   await expect(page.locator('.service-edit')).toHaveCount(1);
   const tile=page.locator('.service-tile.own-service');
   if(!await tile.evaluate(el=>el.open))await tile.locator('summary').click();
   await expect(tile.locator('.service-tile-history')).toBeVisible();
   await page.screenshot({path:path.join(out,`history-${w}.png`)});
   await tile.locator('.service-edit').click();
   await expect(page.locator('#responseComment')).toBeVisible();
   await page.screenshot({path:path.join(out,`status-${w}.png`)});
   await page.locator('#closeResponsePanel').click();
   await page.locator('#cardResponseTools').click();
   await expect(page.locator('#openBriefing')).toBeVisible();
   await page.screenshot({path:path.join(out,`tools-${w}.png`)});
   await page.locator('#closeResponsePanel').click();
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Horizontal overflow');
  }
  await page.locator('#audit').click();
  await expect(page.locator('#auditDialog')).toBeVisible();
  await expect(page.locator('#auditContent')).toContainText('Решения диспетчера');
  await page.screenshot({path:path.join(out,'student-report.png')});
  await page.goto('https://127.0.0.1:3000/map?sid='+encodeURIComponent(live.session_id));
  await expect(page.locator('#mapCanvas')).toBeVisible();
  await expect(page.locator('#status')).not.toContainText('Загрузка карточки');
  await page.locator('#geocodeQuery').fill('Москва Олонецкий 4');
  await page.locator('#geocodeForm button').click();
  await expect(page.locator('#geocodeResults button').first()).toBeVisible({timeout:30000});
  const mapResponse=page.waitForResponse(r=>r.url().includes('/map/features?')&&r.url().includes('zoom=17'));
  await page.locator('#geocodeResults button').first().click();
  const viewport=await mapResponse;
  if(!viewport.ok() || !(await viewport.json()).features.length)throw Error('Map geometry unavailable');
  await expect(page.locator('#status')).toContainText('На карте:');
  await expect.poll(()=>page.evaluate(()=>!window.mapLoading&&!window.mapError)).toBe(true);
  await page.screenshot({path:path.join(out,'map-search.png')});
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Live ARM card/history/status/tools: PASS at 1920, 1280 and 760');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
