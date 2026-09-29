// Живая проверка телефона в браузере на развёрнутом стенде (не на подменённом HTTP).
// Chromium с виртуальным микрофоном: вход обучающегося, регистрация абонента w<номер>,
// эхо-тест 100 с замером задержки и, при LIVE_WEBRTC_CALL=1, учебный звонок занятия,
// который должен прийти в браузер. Пароль — из окружения TRAINER_CHECK_PASSWORD.
const {chromium,expect}=require('playwright/test');
const base=process.env.TRAINER_BASE_URL||'https://localhost:3000';
const username=process.env.TRAINER_CHECK_STUDENT||'kursant1';
(async()=>{
 if(!process.env.TRAINER_CHECK_PASSWORD)throw Error('Set TRAINER_CHECK_PASSWORD');
 const browser=await chromium.launch({headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
 const result={};
 try{
  const context=await browser.newContext({ignoreHTTPSErrors:true,permissions:['microphone']});
  const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{try{localStorage.setItem('onboarding.done','1');}catch{}});
  await page.goto(base+'/login');
  await page.locator('[name=username]').fill(username);await page.locator('[name=password]').fill(process.env.TRAINER_CHECK_PASSWORD);
  await page.locator('#loginForm button[type=submit]').click();await page.waitForURL('**/portal');
  await page.goto(base+'/'+(process.env.LIVE_WEBRTC_LESSON?'?lesson='+process.env.LIVE_WEBRTC_LESSON:''));
  await expect(page.locator('#softphoneToggle')).toBeVisible({timeout:15000});
  result.account=await page.evaluate(()=>{const a=window.trainerSoftphone.account;return a&&{enabled:a.enabled,extension:a.extension,username:a.username};});
  if(!result.account?.enabled)throw Error('Softphone is not enabled for this student');
  await page.evaluate(()=>window.trainerSoftphone.connect(true));
  // Рабочее место само начинает звонок по свежей карточке, поэтому после регистрации
  // телефон может сразу зазвонить.
  await expect.poll(()=>page.evaluate(()=>window.trainerSoftphone.state),{timeout:20000}).toMatch(/^(ready|ringing)$/);
  result.registered=true;
  const answerCall=async()=>{
   await expect(page.locator('#softphoneBar')).toHaveAttribute('data-state','ringing',{timeout:40000});
   result.caller=(await page.locator('#softphoneBar').innerText()).split(String.fromCharCode(10)).slice(0,2).join(' · ');
   await page.getByRole('button',{name:'Ответить'}).click();
   await expect(page.locator('#softphoneBar')).toHaveAttribute('data-state','talking',{timeout:20000});
   await page.waitForTimeout(12000);
   result.call=await page.evaluate(()=>window.trainerSoftphoneStats);
   result.remoteAudio=await page.evaluate(()=>{const a=document.querySelector('audio[data-softphone=remote]');return !!(a&&a.srcObject&&a.srcObject.getAudioTracks().length);});
   await page.evaluate(()=>window.trainerSoftphone.hangup());
   await expect(page.locator('#softphoneBar')).toBeHidden({timeout:10000});
  };
  if(await page.evaluate(()=>window.trainerSoftphone.state)==='ringing'){result.autoCall=true;await answerCall();}
  // Эхо-тест: звук уходит в Asterisk и возвращается; статистика WebRTC даёт задержку.
  await expect.poll(()=>page.evaluate(()=>window.trainerSoftphone.state),{timeout:20000}).toBe('ready');
  await page.evaluate(()=>{document.getElementById('softphoneToggle').click();});
  await page.locator('#softphoneEcho').click();
  await expect(page.locator('#softphoneBar')).toHaveAttribute('data-state','talking',{timeout:20000});
  await page.waitForTimeout(7000);
  result.echo=await page.evaluate(()=>window.trainerSoftphoneStats);
  await page.evaluate(()=>window.trainerSoftphone.hangup());
  await expect(page.locator('#softphoneBar')).toBeHidden({timeout:10000});
  await page.keyboard.press('Escape');
  if(process.env.LIVE_WEBRTC_CALL==='1'&&!result.autoCall){
   const lesson=process.env.LIVE_WEBRTC_LESSON;
   const card=await page.evaluate(async lid=>{const r=await fetch(`/api/v1/student/lessons/${lid}/next`,{method:'POST',headers:{'X-Voice-UI':'1','Content-Type':'application/json'},body:'{}'});return r.json();},lesson);
   if(!card.id)throw Error('Lesson card was not issued');
   await page.evaluate(async sid=>fetch(`/api/v1/student/sessions/${sid}/call`,{method:'POST',headers:{'X-Voice-UI':'1'}}),card.id);
   await answerCall();
  }
  await page.evaluate(()=>window.trainerSoftphone.disconnect(true));
  if(errors.length)throw Error(errors.join('\n'));
  console.log(JSON.stringify(result,null,1));
  console.log('Browser phone (WebRTC): PASS');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
