'use strict';
const form=document.getElementById('loginForm'),status=document.getElementById('status'),controls=document.getElementById('controls');
let bootstrap=false;
let directoryChoice=null;
async function loadDemoAccess(){
 const response=await fetch('/api/v1/auth/demo-access');if(!response.ok)return;
 const data=await response.json();if(!data.enabled||!data.accounts?.length||bootstrap)return;
 const container=document.getElementById('demoAccounts'),roles={admin:'Администратор',teacher:'Преподаватель',student:'Слушатель'};
 for(const account of data.accounts){
  const card=document.createElement('article');card.className='demo-account';
  const title=document.createElement('h3');title.textContent=account.display_name||roles[account.role];
  const values=document.createElement('dl');
  for(const [label,value] of [['Роль',roles[account.role]],['Логин',account.username],['Пароль',account.password],['Рабочее место',account.workstation]]){
   if(!value)continue;const term=document.createElement('dt'),description=document.createElement('dd');term.textContent=label;description.textContent=value;values.append(term,description);
  }
  const choose=document.createElement('button');choose.type='button';choose.textContent='Выбрать';choose.setAttribute('aria-label',`Выбрать: ${title.textContent}`);
  choose.onclick=()=>{
   if(bootstrap)return;
   document.getElementById('mfaBack').click();
   if(directoryChoice)directoryChoice.value='local';
   form.elements.username.value=account.username;form.elements.password.value=account.password;form.elements.workstation.value=account.workstation||'';
   for(const item of container.children)item.removeAttribute('aria-current');card.setAttribute('aria-current','true');status.textContent='Данные подставлены. Нажмите «Продолжить».';form.querySelector('button[type=submit]').focus();
  };
  card.append(title,values,choose);container.append(card);
 }
 document.getElementById('demoAccess').hidden=false;
}
async function init(){
 const response=await fetch('/api/v1/auth/status');if(!response.ok)throw Error('Backend недоступен');
 bootstrap=(await response.json()).bootstrap_required;
 if(!bootstrap){const directoryResponse=await fetch('/api/v1/auth/directory-status');if(directoryResponse.ok&&(await directoryResponse.json()).configured){const label=document.createElement('label');label.textContent='Способ входа';directoryChoice=document.createElement('select');directoryChoice.add(new Option('Локальная учётная запись','local'));directoryChoice.add(new Option('Учебный каталог LDAP / AD','directory'));label.append(directoryChoice);controls.prepend(label);}}
 if(bootstrap){document.getElementById('heading').textContent='Первый запуск';form.elements.password.minLength=12;form.elements.password.autocomplete='new-password';document.getElementById('workstationLabel').hidden=true;}
 else try{form.elements.workstation.value=localStorage.getItem('workstation')||'';}catch{}
 controls.disabled=false;
 if(!bootstrap)loadDemoAccess().catch(()=>{});
}
form.onsubmit=async event=>{event.preventDefault();controls.disabled=true;status.textContent='';try{
 const body={username:form.elements.username.value,password:form.elements.password.value};if(bootstrap)body.display_name=form.elements.username.value;
 const action=bootstrap?'bootstrap':directoryChoice?.value==='directory'?'directory-login':'login';
 const response=await fetch('/api/v1/auth/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Voice-UI':'1'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Проверьте поля формы');
 form.elements.password.value='';
 // Второй фактор: пароль принят, сессия откроется только после кода из приложения.
 if(data.mfa_required){mfaToken=data.mfa_token;form.hidden=true;mfaForm.hidden=false;mfaForm.elements.code.value='';mfaForm.elements.code.focus();return;}
 enter();
 }catch(error){status.textContent=error.message;controls.disabled=false;}};
let mfaToken='';
const mfaForm=document.getElementById('mfaForm');
function enter(){
 localStorage.removeItem('studentSession');
 // Номер АРМ вводится при входе, как в реальной Системе 112, и живёт только в этом
 // браузере: это свойство рабочего места, а не учётной записи. Назначение преподавателя его перекрывает.
 const workstation=(form.elements.workstation?.value||'').trim();
 try{if(workstation)localStorage.setItem('workstation',workstation);else localStorage.removeItem('workstation');}catch{}
 location.replace('/portal');
}
mfaForm.onsubmit=async event=>{event.preventDefault();const controls=document.getElementById('mfaControls');controls.disabled=true;status.textContent='';try{
 const response=await fetch('/api/v1/auth/mfa-login',{method:'POST',headers:{'Content-Type':'application/json','X-Voice-UI':'1'},body:JSON.stringify({mfa_token:mfaToken,code:mfaForm.elements.code.value.trim()})});
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Проверьте код');
 enter();
 }catch(error){status.textContent=error.message;controls.disabled=false;if(/истекло/.test(error.message))document.getElementById('mfaBack').click();}};
document.getElementById('mfaBack').onclick=()=>{mfaToken='';mfaForm.hidden=true;form.hidden=false;controls.disabled=false;};
init().catch(error=>{status.textContent=error.message;});
