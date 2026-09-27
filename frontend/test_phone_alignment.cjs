const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage();
  const html=(await fs.readFile(path.join(__dirname,'student.html'),'utf8')).replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,'');
  await page.setContent(html);
  for(const name of ['student.css','dds-layout.css'])await page.addStyleTag({path:path.join(__dirname,'assets',name)});
  await page.evaluate(()=>{const p=document.querySelector('.card-panel');p.hidden=false;p.classList.add('dds-mode','readonly');document.body.classList.add('card-open');});
  for(const width of [1280,1920]){
   await page.setViewportSize({width,height:900});
   const styles=await page.locator('.phone-field').evaluateAll(fields=>fields.map(field=>{const s=getComputedStyle(field,'::before');return {align:s.alignItems,top:s.paddingTop,bottom:s.paddingBottom,line:s.lineHeight};}));
   assert.equal(styles.length,3);
   for(const s of styles)assert.deepEqual(s,{align:'center',top:'0px',bottom:'0px',line:'23px'});
  }
  await fs.mkdir(path.join(__dirname,'../tmp'),{recursive:true});
  await page.locator('.phone-row').screenshot({path:path.join(__dirname,'../tmp/phone-aligned.png')});
  console.log('Phone alignment: PASS at 1280 and 1920');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
