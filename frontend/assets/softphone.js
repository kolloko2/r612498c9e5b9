'use strict';
// Телефон в браузере (WebRTC) вместо или вместе с IP-телефоном.
// Регистрируется в Asterisk как абонент w<номер> через /sip-ws этого же сервера,
// звук идёт напрямую браузер ↔ Asterisk (DTLS-SRTP). Когда телефон в браузере
// подключён, учебные звонки на номер ученика приходят сюда; иначе — на IP-телефон.
(()=>{
const byId=id=>document.getElementById(id);
const PREF='softphone.enabled',MIC='softphone.mic';
const BROWSER_OFF='Звонки приходят на IP-телефон (MicroSIP). Телефон в браузере работает, когда сайт открыт по https.';
const store={get(k){try{return localStorage.getItem(k);}catch{return null;}},set(k,v){try{v===null?localStorage.removeItem(k):localStorage.setItem(k,v);}catch{}}};
function make(tag,text,className){const x=document.createElement(tag);if(text!==undefined)x.textContent=String(text);if(className)x.className=className;return x;}
let account=null,ua=null,session=null,ringer=null,statsTimer=null,levelStop=null,callStarted=0,clockTimer=null;
let dialNumber='',directoryRequest=0;
let pendingOutgoing=null;
function expectOutgoing(number){pendingOutgoing=number&&ua?.isRegistered?.()?{number:String(number).replace(/\D/g,''),until:Date.now()+30000}:null;}
const remote=make('audio');remote.autoplay=true;remote.setAttribute('data-softphone','remote');

// ---- Интерфейс: кнопка в строке телефона, окно настроек, плашка звонка ----
// Зелёный кружок с трубкой рядом с часами, как просили эксперты: нажатие открывает телефон.
const toggle=make('button',undefined,'phone-circle');toggle.type='button';toggle.id='softphoneToggle';toggle.setAttribute('aria-label','Телефон');
toggle.innerHTML='<span class="phone-dot" aria-hidden="true"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z"/></svg></span><small>телефон</small>';
const bar=make('section',undefined,'softphone-bar');bar.id='softphoneBar';bar.hidden=true;bar.setAttribute('role','region');bar.setAttribute('aria-label','Звонок в браузере');
const dialog=make('dialog',undefined,'softphone-dialog');dialog.id='softphoneDialog';
function setState(state,text){toggle.dataset.state=state;toggle.title=text;const line=byId('softphoneState');if(line){line.textContent=text;line.dataset.state=state;}}

function buildDialog(){
 dialog.replaceChildren();
 const head=make('div',undefined,'dialog-title');const close=make('button','×');close.type='button';close.setAttribute('aria-label','Закрыть');close.onclick=()=>dialog.close();head.append(make('h2','Виртуальный телефон ДДС'),close);
 const info=make('p',account?.enabled?`Учебный номер ${account.extension} (занятие «${account.lesson_title||''}»). Звонки на этот номер приходят в браузер, пока он подключён; иначе — на IP-телефон.`:(account?.reason||'Телефон в браузере недоступен.'));
 const state=make('p','Не подключён','softphone-state');state.id='softphoneState';state.dataset.state=toggle.dataset.state||'off';state.textContent=toggle.title||'Не подключён';
 dialog.append(head,info,state);
 buildDialer();
 if(!account?.enabled)return;
 if(!window.isSecureContext){dialog.append(make('p',BROWSER_OFF,'hint'));return;}
 const micLabel=make('label','Микрофон / гарнитура');const mic=make('select');mic.id='softphoneMic';micLabel.append(mic);
 const meter=make('div',undefined,'softphone-meter');meter.append(make('i'));meter.id='softphoneMeter';meter.setAttribute('aria-label','Уровень микрофона');
 const actions=make('div',undefined,'card-actions');
 const connect=make('button',ua?'Отключить':'Подключить');connect.type='button';connect.className='primary';connect.id='softphoneConnect';
 connect.onclick=()=>ua?disconnect(true):connectPhone(true);
 const echo=make('button','Проверить гарнитуру (эхо 100)');echo.type='button';echo.id='softphoneEcho';echo.disabled=!ua;echo.onclick=echoTest;
 actions.append(connect,echo);
 const stats=make('p','','softphone-stats');stats.id='softphoneStats';
 dialog.append(micLabel,meter,actions,stats,make('p','Нужна гарнитура. Браузер один раз попросит доступ к микрофону.','hint'));
 fillDevices();mic.onchange=()=>{store.set(MIC,mic.value||null);};
}
function buildDialer(){
 const pane=make('section',undefined,'softphone-dialer');
 const label=make('label','Номер контакта ДДС');const input=make('input');input.type='tel';input.id='softphoneNumber';input.value=dialNumber;input.maxLength=40;input.autocomplete='off';input.oninput=()=>{dialNumber=input.value;};label.append(input);
 const keypad=make('div',undefined,'softphone-keypad');keypad.setAttribute('aria-label','Набор номера');
 for(const digit of ['1','2','3','4','5','6','7','8','9','+','0','⌫']){const button=make('button',digit);button.type='button';button.setAttribute('aria-label',digit==='⌫'?'Удалить цифру':`Цифра ${digit}`);button.onclick=()=>{dialNumber=digit==='⌫'?dialNumber.slice(0,-1):(dialNumber+digit).slice(0,40);input.value=dialNumber;};keypad.append(button);}
 const call=make('button','Позвонить','primary');call.id='softphoneDial';call.type='button';
 const error=make('p','','softphone-dial-error');error.setAttribute('role','status');
 call.onclick=async()=>{call.disabled=true;error.textContent='Соединение…';try{if(session)throw Error('Завершите текущий разговор');if(!window.trainerPhone)throw Error('Сначала откройте учебную карточку');await window.trainerPhone.dial(input.value.trim());dialog.close();}catch(e){error.textContent=e.message;}finally{call.disabled=false;}};
 const book=make('div',undefined,'softphone-directory');book.setAttribute('aria-label','Справочник ДДС');book.append(make('h3','Контакты текущего задания'),make('p','Загрузка…'));
 pane.append(label,keypad,call,error,book);dialog.append(pane);
 const request=++directoryRequest;
 Promise.resolve().then(()=>window.trainerPhone?.contacts()||Promise.reject(Error('Сначала откройте учебную карточку'))).then(contacts=>{
  if(request!==directoryRequest)return;
  book.replaceChildren(make('h3',`Справочник ДДС · ${contacts.length}/10`));
  if(!contacts.length)book.append(make('p','Преподаватель ещё не задал контакты для этой карточки.'));
  for(const contact of contacts){const button=make('button',`${contact.name} · ${contact.role_title}\n${contact.number}${contact.required?' · обязательный разговор':''}`);button.type='button';button.disabled=!contact.available;button.title=contact.available?'Выбрать номер':'Сначала назначьте эту бригаду';button.onclick=()=>{dialNumber=contact.number;input.value=dialNumber;input.focus();};book.append(button);}
 }).catch(e=>{if(request===directoryRequest)book.replaceChildren(make('p',e.message));});
}
async function fillDevices(){
 const select=byId('softphoneMic');if(!select||!navigator.mediaDevices?.enumerateDevices)return;
 const devices=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='audioinput');
 select.replaceChildren(new Option('Системный по умолчанию',''));
 devices.forEach((d,i)=>select.add(new Option(d.label||`Микрофон ${i+1}`,d.deviceId)));
 select.value=store.get(MIC)||'';
}
function audioConstraints(){const id=store.get(MIC);return id?{deviceId:{exact:id},echoCancellation:true,noiseSuppression:true}:{echoCancellation:true,noiseSuppression:true};}

// ---- Уровень микрофона и сигнал вызова (WebAudio, без звуковых файлов) ----
function watchLevel(stream){
 const Ctx=window.AudioContext||window.webkitAudioContext;if(!Ctx)return()=>{};
 const ctx=new Ctx(),source=ctx.createMediaStreamSource(stream),analyser=ctx.createAnalyser();analyser.fftSize=512;source.connect(analyser);
 const data=new Uint8Array(analyser.fftSize);const bar=()=>byId('softphoneMeter')?.firstChild;
 const timer=setInterval(()=>{analyser.getByteTimeDomainData(data);let sum=0;for(const v of data){const c=(v-128)/128;sum+=c*c;}const level=Math.min(1,Math.sqrt(sum/data.length)*4);const i=bar();if(i)i.style.width=`${Math.round(level*100)}%`;},100);
 return()=>{clearInterval(timer);source.disconnect();ctx.close();const i=bar();if(i)i.style.width='0';};
}
function startRinging(){
 stopRinging();const Ctx=window.AudioContext||window.webkitAudioContext;if(!Ctx)return;
 const ctx=new Ctx();let on=true;
 const beep=()=>{if(!on)return;const o=ctx.createOscillator(),g=ctx.createGain();o.frequency.value=425;g.gain.value=0.08;o.connect(g).connect(ctx.destination);o.start();o.stop(ctx.currentTime+1);};
 beep();const timer=setInterval(beep,4000);ringer=()=>{on=false;clearInterval(timer);ctx.close();};
}
function stopRinging(){if(ringer){ringer();ringer=null;}}

// ---- Статистика связи: задержка, джиттер, потери ----
async function readStats(pc){
 const report=await pc.getStats();let rtt=null,jitter=null,lost=null,codec=null;const codecs=new Map();
 report.forEach(e=>{if(e.type==='codec')codecs.set(e.id,(e.mimeType||'').replace('audio/',''));});
 report.forEach(e=>{if(e.type==='candidate-pair'&&e.state==='succeeded'&&e.currentRoundTripTime!==undefined)rtt=Math.round(e.currentRoundTripTime*1000);
  if(e.type==='inbound-rtp'&&e.kind==='audio'){if(e.jitter!==undefined)jitter=Math.round(e.jitter*1000);if(e.packetsLost!==undefined)lost=e.packetsLost;if(e.codecId)codec=codecs.get(e.codecId);}});
 return {rtt,jitter,lost,codec};
}
function showStats(s){
 const oneWay=s.rtt===null?null:Math.round(s.rtt/2+(s.jitter||0));
 const text=`${s.codec||'—'} · задержка в одну сторону ≈ ${oneWay===null?'—':oneWay+' мс'} (RTT ${s.rtt??'—'} мс, джиттер ${s.jitter??'—'} мс) · потеряно пакетов ${s.lost??'—'}`;
 for(const id of ['softphoneStats','softphoneBarStats']){const x=byId(id);if(x){x.textContent=text;x.dataset.ok=String(oneWay===null||oneWay<=150);}}
 window.trainerSoftphoneStats=s;
}

// ---- Регистрация ----
async function connectPhone(manual=false){
 if(!account?.enabled||ua)return;
 // Браузер даёт микрофон только на https или localhost: в классе по http звонки идут на IP-телефон.
 if(!window.isSecureContext){store.set(PREF,null);setState('off',BROWSER_OFF);return;}
 if(!window.JsSIP){setState('error','Библиотека телефона не загрузилась');return;}
 try{const probe=await navigator.mediaDevices.getUserMedia({audio:audioConstraints()});probe.getTracks().forEach(t=>t.stop());await fillDevices();}
 catch(error){setState('error','Нет доступа к микрофону: разрешите его в браузере');return;}
 JsSIP.debug.disable('JsSIP:*');
 const socket=new JsSIP.WebSocketInterface(`${location.protocol==='https:'?'wss':'ws'}://${location.host}${account.ws_path}`);
 ua=new JsSIP.UA({sockets:[socket],uri:account.uri,password:account.password,display_name:account.display_name,
  register:true,register_expires:120,session_timers:false,user_agent:'112 AI Trainer · браузер',connection_recovery_min_interval:3,connection_recovery_max_interval:30});
 setState('connecting','Подключение…');
 ua.on('registered',()=>{setState('ready',`Готов: звонки на номер ${account.extension} придут в браузер`);store.set(PREF,'1');refreshDialog();});
 ua.on('unregistered',()=>setState('off','Не подключён'));
 ua.on('registrationFailed',e=>setState('error',`Регистрация не удалась: ${e.cause||'ошибка'}`));
 ua.on('disconnected',()=>{if(ua)setState('connecting','Связь с сервером потеряна, переподключение…');});
 ua.on('newRTCSession',e=>{if(e.originator==='remote')incoming(e.session,e.request);});
 ua.start();
 if(manual)refreshDialog();
}
function disconnect(manual=false){
 hangup();if(ua){const old=ua;ua=null;old.stop();}
 if(manual)store.set(PREF,null);setState('off','Не подключён');refreshDialog();
}
function refreshDialog(){if(dialog.open)buildDialog();}

// ---- Звонки ----
function attach(sessionObject){
 sessionObject.on('peerconnection',({peerconnection})=>{peerconnection.addEventListener('track',ev=>{remote.srcObject=ev.streams[0]||new MediaStream([ev.track]);remote.play().catch(()=>{});});});
 sessionObject.on('confirmed',()=>{stopRinging();callStarted=Date.now();renderBar('talking');const pc=sessionObject.connection;const track=pc?.getSenders().find(s=>s.track?.kind==='audio')?.track;if(track)levelStop=watchLevel(new MediaStream([track]));
  statsTimer=setInterval(()=>readStats(pc).then(showStats).catch(()=>{}),2000);clockTimer=setInterval(()=>renderBar('talking'),1000);});
 const ended=cause=>{stopRinging();clearInterval(statsTimer);clearInterval(clockTimer);statsTimer=clockTimer=null;if(levelStop){levelStop();levelStop=null;}
  remote.srcObject=null;session=null;renderBar(null);if(ua)setState('ready',`Готов: звонки на номер ${account.extension} придут в браузер`);};
 sessionObject.on('ended',ended);sessionObject.on('failed',ended);
}
let caller='';
function incoming(sessionObject,request){
 if(session){sessionObject.terminate({status_code:486,reason_phrase:'Busy Here'});return;}
 session=sessionObject;const identity=sessionObject.remote_identity;caller=[identity?.display_name,identity?.uri?.user].filter(Boolean).join(' · ')||'Учебный вызов';
 attach(sessionObject);startRinging();renderBar('ringing');setState('ringing','Входящий вызов');
 // The application bridges a student-initiated call back to the student's SIP
 // number. Auto-answer only this exact, explicitly requested contact, briefly.
 if(pendingOutgoing&&pendingOutgoing.until>Date.now()&&pendingOutgoing.number===String(identity?.uri?.user||'').replace(/\D/g,'')){
  pendingOutgoing=null;answer();
 }
}
function answer(){if(!session)return;session.answer({mediaConstraints:{audio:audioConstraints(),video:false},pcConfig:{iceServers:[]},rtcOfferConstraints:{offerToReceiveAudio:true}});stopRinging();renderBar('connecting');}
function hangup(){if(session&&!session.isEnded())session.terminate();}
function toggleMute(){if(!session)return;const muted=session.isMuted().audio;muted?session.unmute({audio:true}):session.mute({audio:true});renderBar('talking');}
function echoTest(){
 if(!ua||session)return;caller='Эхо-тест гарнитуры (номер 100)';
 session=ua.call(`sip:${account.echo_test}@trainer112.local`,{mediaConstraints:{audio:audioConstraints(),video:false},pcConfig:{iceServers:[]}});
 attach(session);renderBar('connecting');
}
// Модальное окно делает остальную страницу неактивной: плашка звонка должна
// быть внутри открытого модального окна, иначе «Ответить» нельзя нажать.
function placeBar(){const host=[...document.querySelectorAll('dialog[open]')].reverse().find(d=>d.matches(':modal'))||document.body;if(bar.parentNode!==host)host.append(bar);}
new MutationObserver(()=>{if(!bar.hidden)placeBar();}).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
function renderBar(state){
 if(!state){bar.hidden=true;bar.replaceChildren();return;}
 placeBar();bar.hidden=false;bar.dataset.state=state;bar.replaceChildren();
 const title=make('strong',state==='ringing'?'Входящий вызов':state==='connecting'?'Соединение…':'Разговор');
 const who=make('span',caller);const time=make('span',state==='talking'?new Date(Date.now()-callStarted).toISOString().slice(14,19):'','softphone-time');
 const actions=make('div',undefined,'softphone-actions');
 if(state==='ringing'){const yes=make('button','Ответить');yes.type='button';yes.className='answer';yes.onclick=answer;const no=make('button','Отклонить');no.type='button';no.className='hangup';no.onclick=hangup;actions.append(yes,no);}
 else{const mute=make('button',session?.isMuted?.().audio?'Включить микрофон':'Выключить микрофон');mute.type='button';mute.onclick=toggleMute;mute.disabled=state!=='talking';const end=make('button','Положить трубку');end.type='button';end.className='hangup';end.onclick=hangup;actions.append(mute,end);}
 const stats=make('small','','softphone-bar-stats');stats.id='softphoneBarStats';
 bar.append(title,who,time,actions,stats);
 if(window.trainerSoftphoneStats&&state==='talking')showStats(window.trainerSoftphoneStats);
}

// ---- Запуск ----
async function init(){
 const lesson=new URLSearchParams(location.search).get('lesson');
 try{const response=await fetch('/api/v1/student/softphone'+(lesson&&/^[0-9a-f-]{36}$/i.test(lesson)?'?lesson_id='+lesson:''),{headers:{'X-Voice-UI':'1'}});account=response.ok?await response.json():{enabled:false};}catch{account={enabled:false};}
 // Кнопка в верхней полосе рабочего места: телефон нужен и до открытия карточки.
 const clock=document.querySelector('.console-meta #clock'),cabinet=document.querySelector('.training-strip a[href="/portal"]');
 if(clock)clock.before(toggle);else if(cabinet)cabinet.before(toggle);else document.querySelector('.phone-row .mini-buttons')?.append(toggle);
 document.body.append(bar,dialog,remote);
 setState(account.enabled?'off':'unavailable',account.enabled?'Не подключён':(account.reason||'Недоступен'));
 toggle.onclick=()=>{buildDialog();dialog.showModal();};
 if(account.enabled&&!window.isSecureContext)setState('off',BROWSER_OFF);
 if(account.enabled&&store.get(PREF)==='1')connectPhone();
}
window.trainerSoftphone={connect:connectPhone,disconnect,answer,hangup,expectOutgoing,get state(){return toggle.dataset.state;},get account(){return account;}};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
