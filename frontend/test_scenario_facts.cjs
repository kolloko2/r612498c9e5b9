const {chromium,expect}=require('playwright/test');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.BROWSER_PATH||undefined,args:['--no-sandbox']});
 try{
  const page=await browser.newPage();
  await page.setContent(['prefilled_card','updates','crew_options','dds_expectation'].map(key=>`<label>${key}<textarea data-key="${key}">${['updates','crew_options'].includes(key)?'[]':'{}'}</textarea></label>`).join(''));
  await page.locator('[data-key=dds_expectation]').fill(JSON.stringify({should_accept:true,update_keywords:{working:['повреждена труба']},custom:'preserved'}));
  await page.addScriptTag({path:require('node:path').join(__dirname,'assets/scenario-fields.js')});
  const facts=page.getByLabel('Факты в комментарии к докладу:',{exact:false});
  await expect(facts).toHaveValue('working = повреждена труба');
  await facts.fill('working = повреждена труба; перекрыли воду');
  const saved=JSON.parse(await page.locator('[data-key=dds_expectation]').inputValue());
  require('node:assert/strict').deepEqual(saved.update_keywords,{working:['повреждена труба','перекрыли воду']});
  require('node:assert/strict').equal(saved.custom,'preserved');
  await facts.fill('');
  require('node:assert/strict').deepEqual(JSON.parse(await page.locator('[data-key=dds_expectation]').inputValue()).update_keywords,{});
  console.log('PASS teacher editor: required report facts and preservation');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
