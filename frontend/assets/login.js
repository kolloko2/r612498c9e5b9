'use strict';
const form=document.getElementById('loginForm'),status=document.getElementById('status'),controls=document.getElementById('controls');
let bootstrap=false;
let directoryChoice=null;
async function init(){
 const response=await fetch('/api/v1/auth/status');if(!response.ok)throw Error('Backend недоступен');
 bootstrap=(await response.json()).bootstrap_required;
 if(!bootstrap){const directoryResponse=await fetch('/api/v1/auth/directory-status');if(directoryResponse.ok&&(await directoryResponse.json()).configured){const label=document.createElement('label');label.textContent='Способ входа';directoryChoice=document.createElement('select');directoryChoice.add(new Option('Локальная учётная запись','local'));directoryChoice.add(new Option('Учебный каталог LDAP / AD','directory'));label.append(directoryChoice);controls.prepend(label);}}
 if(bootstrap){document.getElementById('heading').textContent='Первый запуск';form.elements.password.minLength=12;form.elements.password.autocomplete='new-password';document.getElementById('workstationLabel').hidden=true;}
 else try{form.elements.workstation.value=localStorage.getItem('workstation')||'';}catch{}
 controls.disabled=false;
}
form.onsubmit=async event=>{event.preventDefault();controls.disabled=true;status.textContent='';try{
 const body={username:form.elements.username.value,password:form.elements.password.value};if(bootstrap)body.display_name=form.elements.username.value;
 const action=bootstrap?'bootstrap':directoryChoice?.value==='directory'?'directory-login':'login';
 const response=await fetch('/api/v1/auth/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Voice-UI':'1'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Проверьте поля формы');
 form.elements.password.value='';localStorage.removeItem('studentSession');
 // Номер АРМ вводится при входе, как в реальной Системе 112, и живёт только в этом
 // браузере: это свойство рабочего места, а не учётной записи. Назначение преподавателя его перекрывает.
 const workstation=(form.elements.workstation?.value||'').trim();
 try{if(workstation)localStorage.setItem('workstation',workstation);else localStorage.removeItem('workstation');}catch{}
 location.replace('/portal');
 }catch(error){status.textContent=error.message;controls.disabled=false;}};
init().catch(error=>{status.textContent=error.message;});
