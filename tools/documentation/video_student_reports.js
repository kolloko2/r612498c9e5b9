const fs=require('fs');
fs.copyFileSync('/capture/question-padded.wav','/capture/live.wav');
const scenario=JSON.parse(fs.readFileSync('/capture/scenario.json','utf8')).scenario;
for(const update of scenario.updates){
 await page.getByRole('button',{name:'Принять телефонный доклад',exact:true}).first().waitFor({state:'visible',timeout:180000});
 mark('SIP · '+update.unlocks_status);
 await page.getByRole('button',{name:'Принять телефонный доклад',exact:true}).first().click();
 await page.waitForTimeout(52000);
 await page.getByRole('button',{name:'Подтвердить услышанный доклад',exact:true}).first().click();
 await page.waitForTimeout(1000);
 mark('Фиксация · '+update.unlocks_status);
 if(!await page.locator('#responseStatus').isVisible())await page.locator('#cardResponseTools').click();
 await page.locator('#responseStatus').selectOption({label:update.unlocks_status});
 await page.locator('#responseComment').fill(update.text);
 await page.locator('#addResponse').click();
 await page.waitForTimeout(2200);
 await shot();
}
mark('Завершение происшествия и оценка');
await page.locator('#processed').click();
await page.waitForTimeout(1000);
await page.locator('#finish').click();
await page.locator('#confirmFinish').click();
await page.locator('#auditDialog').waitFor({state:'visible'});
await page.waitForTimeout(3000);
return await shot();
