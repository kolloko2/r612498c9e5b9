// Телефон в браузере без Asterisk: подменённый HTTP и SIP-сервер на WebSocket.
// Проверяет регистрацию, входящий вызов, плашку поверх модального окна и отклонение.
const {chromium,expect}=require('playwright/test');
const fs=require('node:fs/promises'),path=require('node:path');
const origin='http://127.0.0.1:3000';
const account={enabled:true,extension:'201',lesson_id:'l1',lesson_title:'Занятие',username:'w201',password:'p'.repeat(32),uri:'sip:w201@trainer112.local',ws_path:'/sip-ws',display_name:'Учебный студент',echo_test:'100'};
const page_html=`<!doctype html><html><head><meta charset="utf-8"></head><body><div class="training-strip"><span>СИСТЕМА-112</span><a href="/portal">Мой кабинет</a></div>
<dialog id="dialogueDialog"><p>Разговор</p></dialog><script src="/assets/vendor/jssip-3.13.8.min.js"></script><script src="/assets/softphone.js"></script></body></html>`;
function header(message,name){const m=message.match(new RegExp('^'+name+':\\s*(.*)$','mi'));return m?m[1].trim():'';}
(async()=>{const browser=await chromium.launch({headless:true,args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream']});
 const sent=[];
 try{
  const context=await browser.newContext({permissions:['microphone']});const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',async route=>{const url=new URL(route.request().url()),p=url.pathname;
   if(p==='/api/v1/student/softphone')return route.fulfill({contentType:'application/json',body:JSON.stringify(account)});
   if(p==='/')return route.fulfill({contentType:'text/html',body:page_html});
   return route.fulfill({body:await fs.readFile(path.join(__dirname,p.slice(1))),contentType:'text/javascript; charset=utf-8'});});
  let socket;
  await page.routeWebSocket(/\/sip-ws$/,ws=>{socket=ws;ws.onMessage(message=>{
   sent.push(message);const first=message.split('\r\n')[0];
   if(first.startsWith('REGISTER')){ws.send(['SIP/2.0 200 OK','Via: '+header(message,'Via')+';received=127.0.0.1','From: '+header(message,'From'),'To: '+header(message,'To')+';tag=srv','Call-ID: '+header(message,'Call-ID'),'CSeq: '+header(message,'CSeq'),'Contact: '+header(message,'Contact')+';expires=120','Content-Length: 0','',''].join('\r\n'));}
  });});
  await page.goto(origin+'/');
  await expect(page.locator('#softphoneToggle')).toBeVisible();
  // Кнопка стоит в верхней полосе рабочего места, до «Мой кабинет».
  if(await page.evaluate(()=>document.getElementById('softphoneToggle').nextElementSibling?.getAttribute('href'))!=='/portal')throw Error('Toggle is not in the strip');
  await page.evaluate(()=>window.trainerSoftphone.connect(true));
  await expect.poll(()=>page.evaluate(()=>window.trainerSoftphone.state)).toBe('ready');
  // Рабочее место открывает модальное окно разговора — плашка должна оказаться внутри него.
  await page.evaluate(()=>document.getElementById('dialogueDialog').showModal());
  const contact=header(sent.find(m=>m.startsWith('REGISTER')),'Contact').match(/<([^>]+)>/)[1];
  const sdp=['v=0','o=- 1 1 IN IP4 127.0.0.1','s=-','c=IN IP4 127.0.0.1','t=0 0','m=audio 20000 UDP/TLS/RTP/SAVPF 0','a=rtpmap:0 PCMU/8000','a=sendrecv',''].join('\r\n');
  socket.send([`INVITE ${contact} SIP/2.0`,'Via: SIP/2.0/WSS asterisk:8089;branch=z9hG4bKtest1','Max-Forwards: 70','From: "Старший бригады" <sip:+79000000101@asterisk>;tag=caller1',`To: <${contact}>`,'Call-ID: call-test-1','CSeq: 1 INVITE','Contact: <sip:asterisk@asterisk:8089;transport=ws>','Content-Type: application/sdp',`Content-Length: ${Buffer.byteLength(sdp)}`,'',sdp].join('\r\n'));
  await expect(page.locator('#softphoneBar')).toHaveAttribute('data-state','ringing');
  await expect(page.locator('#softphoneBar')).toContainText('Старший бригады');
  if(!await page.evaluate(()=>!!document.querySelector('#dialogueDialog #softphoneBar')))throw Error('Call bar is outside the open modal dialog');
  await expect.poll(()=>sent.some(m=>m.startsWith('SIP/2.0 180'))).toBe(true);
  await page.getByRole('button',{name:'Отклонить'}).click();
  await expect.poll(()=>sent.some(m=>/^SIP\/2\.0 (486|480|603)/.test(m))).toBe(true);
  await expect(page.locator('#softphoneBar')).toBeHidden();
  if(errors.length)throw Error(errors.join('\n'));
  console.log('Softphone browser flow: PASS (mock SIP over WebSocket)');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
