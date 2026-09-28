'use strict';
// Derive guidance from received facts only. Never read the private marking rubric.
(function(root){
 function nextAction(s){
  return s?.practice_with_hints && s.exercise_mode==='actions' ? s.practice_hint || null : null;
 }
 if(typeof module!=='undefined')module.exports={nextAction};
 if(!root.document)return;
 let state=null,enabled=false;
 const $=id=>document.getElementById(id);
 const panel=document.createElement('aside');panel.id='ddsCoach';panel.hidden=true;panel.setAttribute('aria-label','Практика диспетчера');
 const title=document.createElement('h3'),text=document.createElement('p'),go=document.createElement('button'),close=document.createElement('button');
 const sample=document.createElement('blockquote'),insert=document.createElement('button');
 go.type=close.type=insert.type='button';go.textContent='Показать, куда нажать';close.textContent='Скрыть подсказки';insert.textContent='Вставить текст';
 sample.className='dds-coach-sample';insert.className='dds-coach-insert';
 panel.append(title,text,sample,insert,go,close);document.body.append(panel);
 // Вставляет образец в нужное поле; если окно доклада ещё не открыто — подскажет.
 insert.onclick=()=>{const step=nextAction(state),field=step?.fill&&$(step.fill);
  if(!field||field.closest('dialog:not([open])')||field.closest('[hidden]')){insert.textContent='Сначала откройте окно, затем вставьте';setTimeout(()=>insert.textContent='Вставить текст',2500);return;}
  field.value=step.sample;field.dispatchEvent(new Event('input',{bubbles:true}));field.focus();};
 function key(){return 'ddsCoach:'+state?.id;}
 // Подсказка встаёт рядом с элементом, о котором говорит: над ним или под ним.
 function place(){
  if(panel.hidden)return;
  // Модальное окно (доклад, ошибка в 112) перекрывает всё остальное: подсказка
  // переезжает в само окно и встаёт сбоку от него, чтобы её можно было нажать.
  const dialog=document.querySelector('dialog[open]'),host=dialog||document.body;
  if(panel.parentElement!==host)host.append(panel);
  panel.style.width='';panel.style.maxHeight='';
  const step=nextAction(state),target=step&&$(step.target);
  // Не закрывать поля, которые нужно заполнить: якорь — вся панель реагирования или окно.
  const anchor=dialog||target?.closest('#responseSection, .response-section')||target,box=anchor?.getBoundingClientRect();
  if(!box||!box.width||!box.height){panel.style.left='';panel.style.top='';panel.style.right='';return;}
  let width=panel.offsetWidth;
  const right=innerWidth-box.right-24,left=box.left-24;
  let x,y;
  if(dialog){
   const room=Math.max(right,left);if(room<width&&room>=220){width=room;panel.style.width=room+'px';}
   x=right>=width?box.right+12:left>=width?box.left-width-12:innerWidth-width-10;y=box.top;
  }else{
   // Справа над панелью: слева адрес и описание — их ученик читает во время шага.
   x=Math.min(box.right-width,innerWidth-width-10);y=box.top-panel.offsetHeight-12;
   if(y<10){
    // Над панелью мало места: ниже — если помещается, иначе ужать и прокручивать.
    if(box.bottom+12+panel.offsetHeight<=innerHeight-10)y=box.bottom+12;
    else{panel.style.maxHeight=Math.max(160,box.top-22)+'px';y=10;}
   }
  }
  const height=panel.offsetHeight;
  y=Math.max(10,Math.min(y,innerHeight-height-10));x=Math.max(10,x);
  panel.style.right='auto';panel.style.left=x+'px';panel.style.top=y+'px';
 }
 function render(){
  const step=nextAction(state);panel.hidden=!state?.practice_with_hints||!enabled||!step||$('cardPanel')?.hidden;
  document.querySelectorAll('.dds-coach-target').forEach(n=>n.classList.remove('dds-coach-target'));
  if(panel.hidden)return;title.textContent=step.title;text.textContent=step.text;
  sample.hidden=!step.sample;insert.hidden=!step.sample||!step.fill;sample.textContent=step.sample?'Например: «'+step.sample+'»':'';
  requestAnimationFrame(place);
 }
 addEventListener('resize',()=>requestAnimationFrame(place));
 // Открытие и закрытие окон меняет место подсказки.
 new MutationObserver(()=>requestAnimationFrame(place)).observe(document.body,{subtree:true,attributes:true,attributeFilter:['open']});
 close.onclick=()=>{enabled=false;sessionStorage.setItem(key(),'off');render();};
 go.onclick=()=>{
  const step=nextAction(state);if(!step)return;
  if(['responseStatus','responseComment','crewSelect','openBriefing','requestProgress'].includes(step.target)){
   document.querySelectorAll('#services details[open]').forEach(d=>d.open=false);
   $('cardPanel').classList.remove('response-compact');$('cardPanel').classList.add('response-open');
  }
  const target=$(step.target);target?.scrollIntoView({block:'nearest'});target?.focus();target?.classList.add('dds-coach-target');
  requestAnimationFrame(place);
 };
 root.updateDdsCoach=s=>{const changed=state?.id!==s?.id;state=s;if(!s?.practice_with_hints)enabled=false;else if(changed){const saved=sessionStorage.getItem(key());enabled=saved!=='off';}render();};
 root.startDdsCoach=()=>{if(!state?.practice_with_hints)return;enabled=true;sessionStorage.setItem(key(),'on');render();};
})(typeof window==='undefined'?globalThis:window);
