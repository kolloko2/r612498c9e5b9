'use strict';
// Двухфакторный вход в кабинете: подключение приложения-аутентификатора,
// резервные коды и отключение. Если администратор сделал второй фактор
// обязательным для роли, кабинет открывается только после настройки.
(()=>{
const byId=id=>document.getElementById(id);
function make(tag,text,className){const x=document.createElement(tag);if(text!==undefined)x.textContent=String(text);if(className)x.className=className;return x;}
async function call(path,method='GET',body){
 const options={method,headers:{'X-Voice-UI':'1'}};
 if(body!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
 const response=await fetch(path,options);const data=response.status===204?null:await response.json().catch(()=>null);
 if(!response.ok)throw Error(typeof data?.detail==='string'?data.detail:'Не удалось выполнить действие');
 return data;
}
function panel(){
 let box=byId('mfaPanel');
 if(!box){box=make('section',undefined,'panel mfa-panel');box.id='mfaPanel';document.querySelector('main').append(box);}
 return box;
}
function message(box,text,error=false){let line=box.querySelector('.mfa-status');if(!line){line=make('p',undefined,'mfa-status');line.setAttribute('role','status');box.append(line);}line.textContent=text;line.classList.toggle('error',error);}
function codeForm(label,action,onSubmit){
 const form=make('form',undefined,'inline'),input=document.createElement('input');
 input.required=true;input.minLength=6;input.maxLength=20;input.inputMode='numeric';input.autocomplete='one-time-code';input.placeholder='123 456';input.setAttribute('aria-label',label);
 const submit=make('button',action);submit.className='primary';form.append(input,submit);
 form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{await onSubmit(input.value.trim());}catch(error){message(form.parentElement,error.message,true);submit.disabled=false;input.select();}};
 return form;
}
function showRecovery(box,codes){
 box.replaceChildren(make('h2','Двухфакторный вход включён'),
  make('p','Сохраните резервные коды. Каждый срабатывает один раз вместо кода из приложения, если телефона нет под рукой. Больше они показаны не будут.'));
 const list=make('ol',undefined,'mfa-codes');for(const code of codes)list.append(make('li',code));box.append(list);
 const actions=make('div',undefined,'card-actions'),download=make('button','Скачать коды .txt'),done=make('button','Коды сохранены');done.className='primary';
 download.type=done.type='button';
 download.onclick=()=>{const url=URL.createObjectURL(new Blob([`Резервные коды входа · Тренажёр 112\n\n${codes.join('\n')}\n`],{type:'text/plain'})),a=document.createElement('a');a.href=url;a.download='trainer112-recovery-codes.txt';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 done.onclick=()=>location.reload();
 actions.append(download,done);box.append(actions);
}
async function startSetup(box){
 const data=await call('/api/v1/auth/mfa/setup','POST');
 box.replaceChildren(make('h2','Подключение приложения-аутентификатора'));
 const steps=make('ol',undefined,'mfa-steps');
 steps.append(make('li','Установите на телефон любое приложение для одноразовых кодов: Яндекс Ключ, Google Authenticator, Microsoft Authenticator, FreeOTP.'),
  make('li','Отсканируйте QR-код или введите ключ вручную (тип — по времени, 6 цифр, 30 секунд).'),
  make('li','Введите код, который показало приложение.'));
 box.append(steps);
 const pair=make('div',undefined,'mfa-pair');
 if(data.qr_svg){const svg=new DOMParser().parseFromString(data.qr_svg,'text/html').querySelector('svg');if(svg){const image=document.importNode(svg,true);image.setAttribute('role','img');image.setAttribute('aria-label','QR-код для приложения-аутентификатора');pair.append(image);}}
 const key=make('div');key.append(make('span','Ключ для ручного ввода'),make('code',data.secret.match(/.{1,4}/g).join(' '),'mfa-secret'));pair.append(key);box.append(pair);
 box.append(codeForm('Код из приложения','Подтвердить',async code=>{const result=await call('/api/v1/auth/mfa/enable','POST',{code});showRecovery(box,result.recovery_codes);}));
}
async function render(me){
 const status=me.mfa||{},box=panel();
 box.replaceChildren(make('h2','Двухфакторный вход'));
 const pending=status.required&&!status.enabled;
 if(pending){
  for(const id of ['adminPanel','teacherPanel','studentPanel'])if(byId(id))byId(id).hidden=true;
  document.querySelector('main').prepend(box);
  box.append(make('p','Администратор сделал второй фактор обязательным для вашей роли. Подключите приложение-аутентификатор — после этого кабинет откроется.','mfa-required'));
 }
 if(status.enabled){
  box.append(make('p',`Включён: при входе после пароля нужен код из приложения. Осталось резервных кодов: ${status.recovery_codes_left}.`));
  if(status.required)box.append(make('p','Для вашей роли второй фактор обязателен. Если телефон утерян, обратитесь к администратору: он сбросит настройку.','hint'));
  else{
   const details=make('details'),summary=make('summary','Отключить двухфакторный вход');details.append(summary,make('p','Подтвердите отключение кодом из приложения или резервным кодом.'),
    codeForm('Код для отключения','Отключить',async code=>{await call('/api/v1/auth/mfa/disable','POST',{code});location.reload();}));
   box.append(details);
  }
  return;
 }
 box.append(make('p','Вход по паролю и одноразовому коду из приложения на телефоне. Коды считаются на телефоне без интернета, поэтому способ работает в закрытом контуре.'));
 const start=make('button','Включить двухфакторный вход');start.type='button';start.className='primary';
 start.onclick=async()=>{start.disabled=true;try{await startSetup(box);}catch(error){message(box,error.message,true);start.disabled=false;}};
 box.append(start);
}
window.trainerMfa={render};
})();
