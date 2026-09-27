await page.locator('#finishBriefing').waitFor({state:'visible',timeout:55000});
await page.waitForTimeout(10000);
mark('Подтверждение телефонограммы');
await page.locator('#briefingRecipient').fill('Старший бригады 01-3');
await page.locator('#finishBriefing').click();
await page.waitForTimeout(1500);
await page.locator('[data-close="briefingDialog"]').click();
return await shot();
