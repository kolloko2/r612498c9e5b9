// Task-local recorder. The control port is loopback-only inside Docker.
const {chromium}=require('playwright');
const fs=require('fs'),http=require('http'),path=require('path');
const out='/capture';
fs.mkdirSync(out,{recursive:true});
let browser,context,page,started,video,marks=[],name;
const base='https://127.0.0.1:3000';
const password=fs.readFileSync('/project/tools/seed_demo.py','utf8').match(/^PASSWORD = "([^"]+)"/m)[1];
async function begin(label,user){
 name=label; marks=[];
 context=await browser.newContext({viewport:{width:1600,height:1000},recordVideo:{dir:out,size:{width:1600,height:1000}},locale:'ru-RU'});
 page=await context.newPage();started=Date.now();video=page.video();
 page.on('dialog',d=>d.accept());
 await page.goto(base+'/login');
 await page.locator('[name=username]').fill(user);
 await page.locator('[name=password]').fill(password);
 await page.locator('button[type=submit]').click();
 await page.waitForURL('**/portal');
 await page.waitForTimeout(1500);
 await page.addLocatorHandler(page.locator('.onboarding'),()=>page.locator('[data-onboarding="stop"]').click());
 mark('Начало');
}
function mark(title,focus=null){marks.push({t:(Date.now()-started)/1000,title,focus});fs.writeFileSync(out+'/'+name+'.json',JSON.stringify({started,marks},null,2));return marks.at(-1);}
async function shot(){await page.screenshot({path:out+'/current.png'});return (await page.locator('body').innerText()).slice(0,15000);}
async function end(){mark('Конец');await context.close();await video.saveAs(out+'/'+name+'.webm');context=null;return name;}
(async()=>{
 browser=await chromium.launch({executablePath:'/usr/bin/chromium',headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 http.createServer(async(req,res)=>{
  if(req.method!=='POST'){res.writeHead(405);return res.end();}
  let body='';for await(const part of req)body+=part;
  try{const command=JSON.parse(body);const run=new Function('page','begin','end','mark','shot','base','require','return (async()=>{'+command.code+'})()');
   const result=await run(page,begin,end,mark,shot,base,require);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({ok:true,result}));
  }catch(e){if(page)await page.screenshot({path:out+'/error.png'}).catch(()=>{});res.end(JSON.stringify({ok:false,error:e.message}));}
 }).listen(8765,'127.0.0.1',()=>console.log('Recorder ready'));
})();
