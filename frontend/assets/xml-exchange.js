'use strict';
// XML imports fill a reviewable form only; they never publish or submit it.
(() => {
 const el=id=>document.getElementById(id);
 const save=(doc,name)=>{const url=URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(doc)],{type:'application/xml'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
 const documentFor=name=>{const doc=document.implementation.createDocument('',name);doc.documentElement.setAttribute('version','1');return doc;};
 const add=(doc,parent,name,text)=>{const node=doc.createElement(name);node.textContent=text;parent.append(node);return node;};
 async function read(file,name){
  if(!file||file.size>1024*1024)throw Error('XML: выберите файл до 1 МиБ');
  const text=await file.text();if(/<!DOCTYPE|<!ENTITY/i.test(text))throw Error('DTD и сущности XML запрещены');
  const doc=new DOMParser().parseFromString(text,'application/xml'),root=doc.documentElement;
  if(doc.querySelector('parsererror')||root.tagName!==name||root.getAttribute('version')!=='1'||root.namespaceURI||root.attributes.length!==1)throw Error('Неверный формат или версия XML');
  return root;
 }
 function toolbar(parent,exportFn,importFn){
  const row=document.createElement('div');row.className='response-tools';
  const output=document.createElement('button'),input=document.createElement('input'),label=document.createElement('label'),message=document.createElement('p');
  output.type='button';output.textContent='Скачать XML';input.type='file';input.accept='.xml,application/xml';label.textContent='Загрузить XML в форму ';label.append(input);message.setAttribute('role','status');
  output.onclick=()=>{try{exportFn();message.textContent='XML сформирован.';}catch(error){message.textContent=error.message;}};
  input.onchange=async()=>{try{await importFn(input.files[0]);message.textContent='XML загружен в форму. Проверьте данные и сохраните вручную.';}catch(error){message.textContent=error.message;}finally{input.value='';}};
  row.append(output,label);parent.append(row,message);
 }
 if(el('materialForm')){
  const fields={title:['materialTitle',160],description:['description',2000],body:['body',100000],difficulty:['difficulty',30],dds_profile:['profile',30]};
  toolbar(el('materialForm'),()=>{const doc=documentFor('trainer112-material');for(const [name,[id]] of Object.entries(fields))add(doc,doc.documentElement,name,el(id).value);save(doc,'material.xml');},async file=>{
   const root=await read(file,'trainer112-material'),values={};
   for(const child of root.children){const spec=fields[child.tagName];if(!spec||child.children.length||child.attributes.length||Object.hasOwn(values,child.tagName)||child.textContent.length>spec[1])throw Error('Неизвестное, повторное или слишком длинное поле материала');values[child.tagName]=child.textContent;}
   if(Object.keys(values).length!==Object.keys(fields).length||values.title.trim().length<3)throw Error('XML материала неполон');
   for(const name of ['difficulty','dds_profile'])if(![...el(fields[name][0]).options].some(o=>o.value===values[name]))throw Error('Неизвестный уровень или профиль');
   for(const [name,[id]] of Object.entries(fields))el(id).value=values[name];
   el('published').checked=false;el('materialForm').dispatchEvent(new Event('input',{bubbles:true}));
  });
 }
 if(el('lessonForm')){
  const places=()=>new Map([...el('lessonPlaces').querySelectorAll('[data-place-student-id]')].map(input=>[input.dataset.placeStudentId,input]));
  toolbar(el('lessonForm'),()=>{
   const map=places();if(!map.size)throw Error('Сначала выберите группу с обучающимися');
   const doc=documentFor('trainer112-workstations');add(doc,doc.documentElement,'group',el('lessonGroup').value);
   for(const [id,input] of map){const station=add(doc,doc.documentElement,'station',input.value);station.setAttribute('student',id);}
   save(doc,'workstations.xml');
  },async file=>{
   const root=await read(file,'trainer112-workstations'),map=places(),pending=new Map();let group='';
   for(const child of root.children){
    if(child.tagName==='group'&&!group&&!child.attributes.length&&!child.children.length){group=child.textContent;continue;}
    const id=child.getAttribute('student');
    if(child.tagName!=='station'||child.attributes.length!==1||child.children.length||!map.has(id)||pending.has(id)||child.textContent.length>80)throw Error('Неизвестное или повторное рабочее место');pending.set(id,child.textContent);
   }
   if(group!==el('lessonGroup').value||!pending.size)throw Error('XML относится к другой группе или не содержит рабочих мест');
   for(const [id,value] of pending){map.get(id).value=value;map.get(id).dispatchEvent(new Event('input',{bubbles:true}));}
  });
 }
})();
