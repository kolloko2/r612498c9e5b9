// Standalone ticket-catalog browser regression with intercepted HTTP only: no
// Backend, database, model or external network. Runs in Chromium by default and
// in Firefox with BROWSER_ENGINE=firefox, covering both engines named in the
// task statement (Chrome and Яндекс.Браузер share the Chromium engine).
const engines=require('playwright/test');
const {expect}=engines;
const fs=require('node:fs/promises'),path=require('node:path');
const root=__dirname,origin='http://127.0.0.1:3000',shots=path.join(root,'..','tmp');
const engineName=process.env.BROWSER_ENGINE||'chromium';

const draft=(ticket,call,title,category,extra={})=>({
 id:`ticket-${String(ticket).padStart(2,'0')}-${call}`,call,title,category_id:category,
 difficulty:'basic',dds_profile:category==='fire'?'fire':'general',published_scenario_id:null,
 classified_by:'горит',
 scenario:{id:`ticket-${String(ticket).padStart(2,'0')}-${call}`,title,category_id:category,
  difficulty:'basic',dds_profile:category==='fire'?'fire':'general',learning_objectives:'Учебная цель',
  description:`Билет ${ticket}, вызов ${call}`,victim_name:'Синтетический Заявитель Тестович',
  incident:`${title}, синтетические обстоятельства, +7 900 000-00-01`,
  location:'Вымышленный адрес, дом 1 (уточнение источника)',
  known_facts:['Заявитель: Синтетический Заявитель Тестович','Телефон заявителя: +7 900 000-00-01',title],
  unknown_facts:extra.unknown||[],emotion:'Обеспокоена',behavior:'Отвечает на вопросы',
  opening:'Это учебный звонок.',enabled:false},
 rubric:{title:`Эталон билета ${ticket}, вызов ${call}`,time_limit_seconds:30,
  criteria:[{id:'summary',label:'Суть происшествия в описании',field:'description',
             mode:'contains_all',expected:[title],weight:1}]},
 ticket,source_page:ticket});

const catalog={
 source:{document:'Датасет/Билеты- задачи по C 112 . АГС_ГСИ.pdf',transcription:'manual'},
 phones:'synthetic',metadata:{tickets:2,drafts:4,unclassified:[]},
 drafts:[draft(1,1,'Горит мусорный контейнер','fire'),
         draft(1,2,'Дерутся во дворе','public'),
         draft(1,3,'Ребёнок упал с велосипеда','medical',{unknown:['Точный возраст неизвестен']}),
         draft(2,1,'Задымление на платформе','fire')],
};
let publishBody=null,publishCalls=0;
const copy=value=>JSON.parse(JSON.stringify(value));

function listing(){
 const tickets={};
 for(const item of catalog.drafts){
  const entry=tickets[item.ticket]||(tickets[item.ticket]={number:item.ticket,page:item.source_page,calls:[]});
  entry.calls.push({id:item.id,call:item.call,title:item.title,category_id:item.category_id,
                    difficulty:item.difficulty,dds_profile:item.dds_profile,
                    published_scenario_id:item.published_scenario_id});}
 return {source:catalog.source,phones:catalog.phones,metadata:catalog.metadata,
         tickets:Object.values(tickets).sort((a,b)=>a.number-b.number)};
}

async function serve(page){
 await page.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url()),p=url.pathname;
  if(url.origin!==origin)return route.abort();
  if(p.startsWith('/api/')){
   let data,status=200;
   if(p==='/api/v1/auth/me')data={id:'teacher-test',role:'teacher',display_name:'Учебный преподаватель',active:true};
   else if(p==='/api/v1/instructor/tickets')data=listing();
   else if(p==='/api/v1/instructor/tickets/publish'){
    publishCalls++;publishBody=request.postDataJSON();
    const published=publishBody.draft_ids.map(id=>{
     const item=catalog.drafts.find(entry=>entry.id===id);
     if(!item)throw Error('Unknown fixture draft '+id);
     const created=!item.published_scenario_id;
     if(created)item.published_scenario_id=id+'-abcd1234';
     return {draft_id:id,scenario_id:item.published_scenario_id,created};});
    data={published};status=201;
   }else{
    const match=p.match(/^\/api\/v1\/instructor\/tickets\/(\d+)$/);
    if(!match)throw Error('Unexpected API '+request.method()+' '+p);
    const number=Number(match[1]),items=catalog.drafts.filter(item=>item.ticket===number);
    if(!items.length){data={detail:'Билет не найден'};status=404;}
    else data={number,page:items[0].source_page,source:catalog.source,calls:items.map(copy)};
   }
   return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  }
  const file=p==='/'||p==='/tickets'?'tickets.html':p.slice(1);
  if(file.includes('..'))throw Error('Unsafe test asset');
  const body=await fs.readFile(path.join(root,file));
  await route.fulfill({body,contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
 });
}

(async()=>{
 await fs.mkdir(shots,{recursive:true});
 const engine=engines[engineName];
 if(!engine)throw Error('Unknown browser engine '+engineName);
 const browser=await engine.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1400,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept());
  await serve(page);await page.goto(origin+'/tickets');

  await expect(page.locator('#provenance')).toContainText('билетов 2, заготовок 4');
  await expect(page.locator('#provenance')).toContainText('транскрипция ручная со сканов');
  await expect(page.locator('#ticketList li')).toHaveCount(2);
  await expect(page.locator('#publish')).toBeDisabled();

  await page.locator('#ticketList li:first-child button').click();
  await expect(page.locator('#detailTitle')).toContainText('Билет 1 · страница источника 1');
  await expect(page.locator('#calls article')).toHaveCount(3);
  // Заготовка показывает норматив и черновик эталона до публикации.
  await expect(page.locator('#calls')).toContainText('Черновик эталона · норматив 30 с');
  await expect(page.locator('#calls')).toContainText('Заявителю неизвестно');
  await expect(page.locator('#calls')).toContainText('+7 900 000-00-01');

  await page.locator('input[data-draft="ticket-01-1"]').check();
  await expect(page.locator('#publish')).toBeEnabled();
  await page.locator('input[data-draft="ticket-01-3"]').check();
  await page.screenshot({path:path.join(shots,`tickets-${engineName}.png`)});
  await page.locator('#publish').click();

  await expect(page.locator('#status')).toContainText('Создано сценариев: 2');
  if(publishCalls!==1)throw Error('Publish was not a single request');
  if(publishBody.draft_ids.join(',')!=='ticket-01-1,ticket-01-3')throw Error('Invalid publish payload');
  await expect(page.locator('#ticketList li:first-child button')).toContainText('опубликовано 2 из 3');
  await expect(page.locator('input[data-draft="ticket-01-1"]')).toBeDisabled();
  await expect(page.locator('#calls')).toContainText('Уже опубликован как сценарий ticket-01-1-abcd1234');

  // Повторная публикация оставшегося вызова не трогает уже созданные сценарии.
  await page.locator('input[data-draft="ticket-01-2"]').check();
  await page.locator('#publish').click();
  await expect(page.locator('#status')).toContainText('Создано сценариев: 1');
  await expect(page.locator('#ticketList li:first-child button')).toContainText('опубликовано 3 из 3');

  // Мобильная ширина: колонки складываются, горизонтальной прокрутки нет.
  await page.setViewportSize({width:400,height:900});
  await expect(page.locator('#ticketList')).toBeVisible();
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth-document.documentElement.clientWidth);
  if(overflow>1)throw Error('Horizontal overflow at 400px: '+overflow);

  if(errors.length)throw Error(errors.join('\n'));
  console.log(`Tickets browser flow (${engineName}): PASS (mock HTTP, no Backend/database/model)`);
  console.log(path.join(shots,`tickets-${engineName}.png`));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
