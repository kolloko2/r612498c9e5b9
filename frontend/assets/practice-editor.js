'use strict';
// Authored content is saved with the scenario, never generated during a lesson.
(()=>{
 const source=document.querySelector('[data-key="practice_plan"]'),box=el('practiceSteps');
 const phases={accept:'При поступлении карточки',refused:'После отказа',assign:'Перед назначением бригады',crew:'Перед докладом бригаде',superior:'Перед докладом руководителю',correction:'После получения уточнения',update:'После получения доклада',processed:'После итогового статуса',finish:'Перед завершением',wait:'Ожидание доклада',completed:'После завершения'};
 function render(){
  box.replaceChildren();
  const steps=source.value?JSON.parse(source.value):[];
  el('practiceState').textContent=loaded?.practice_approved_version?'Подсказки утверждены.':'Подсказки не утверждены.';
  for(const step of steps){
   const section=document.createElement('fieldset'),legend=document.createElement('legend');
   legend.textContent=phases[step.phase]+(step.update_id?' · '+step.update_id:'');section.append(legend);
   for(const [key,labelText,max] of [['title','Заголовок',160],['text','Объяснение действия',2000],['sample','Пример комментария (необязательно)',1000]]){
    const label=document.createElement('label'),input=document.createElement('textarea');label.textContent=labelText;input.value=step[key]||'';input.maxLength=max;input.required=key!=='sample';input.rows=key==='text'?3:2;
    input.oninput=()=>{step[key]=input.value;source.value=JSON.stringify(steps);};label.append(input);section.append(label);
   }
   box.append(section);
  }
 }
 addEventListener('scenario-filled',render);
 el('scenarioForm').addEventListener('input',()=>{el('practiceState').textContent='Есть изменения. После проверки утвердите подсказки заново.';});
 el('practiceDraft').onclick=()=>run(async()=>{
  if(source.value&&!confirm('Заменить текущие подсказки новым черновиком?'))return;
  const value=read();value.practice_plan=[];
  const result=await api('/api/scenarios/practice-draft','POST',value);
  source.value=JSON.stringify(result.steps);render();dirty=true;el('practiceState').textContent='Черновик. Проверьте все шаги перед утверждением.';
 });
 el('practiceApprove').onclick=()=>run(async()=>{
  if(!el('scenarioForm').reportValidity())return;
  if(!confirm('Вы проверили все подсказки и утверждаете их для выдачи ученикам?'))return;
  let value=read();
  if(!loaded){const saved=await api('/api/scenarios','POST',value);fill(saved,true);value=read();}
  value.version=loaded.version;value.practice_confirm=true;
  const saved=await api('/api/scenarios/'+encodeURIComponent(loaded.id),'PUT',value);
  fill(saved,true);await list(saved.id);status('Подсказки утверждены. При выдаче включите «Практика с подсказками».');
 });
 render();
})();
