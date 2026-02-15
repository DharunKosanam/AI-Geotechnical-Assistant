import { test, expect } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

const screenshotsDir = path.join(__dirname, 'quick-test-screenshots');
if (!fs.existsSync(screenshotsDir)) {
  fs.mkdirSync(screenshotsDir, { recursive: true });
}

test('Quick Thread Switch Test', async ({ page }) => {
  test.setTimeout(120000); // 2 minutes
  console.log('\n=== STEP 1: NAVIGATE ===');
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: path.join(screenshotsDir, '1-page-load.png'), fullPage: true });
  console.log('✓ Page loaded');

  console.log('\n=== STEP 2: SEND SPT MESSAGE ===');
  const input1 = await page.locator('textarea, input[type="text"]').last();
  await input1.fill('What is SPT test?');
  await input1.press('Enter');
  console.log('✓ Sent: "What is SPT test?"');
  await page.waitForTimeout(20000);
  await page.screenshot({ path: path.join(screenshotsDir, '2-spt-response.png'), fullPage: true });
  const sptVisible1 = await page.locator('text="What is SPT test?"').isVisible().catch(() => false);
  console.log(`✓ SPT message visible: ${sptVisible1}`);

  console.log('\n=== STEP 3: NEW CHAT ===');
  const newChatBtn = await page.locator('button:has-text("New Chat"), button:has-text("New")').first();
  await newChatBtn.click();
  await page.waitForTimeout(3000);
  await page.screenshot({ path: path.join(screenshotsDir, '3-new-chat.png'), fullPage: true });
  console.log('✓ New chat created');

  console.log('\n=== STEP 4: SEND LIQUEFACTION MESSAGE ===');
  const input2 = await page.locator('textarea, input[type="text"]').last();
  await input2.fill('Explain soil liquefaction');
  await input2.press('Enter');
  console.log('✓ Sent: "Explain soil liquefaction"');
  await page.waitForTimeout(20000);
  await page.screenshot({ path: path.join(screenshotsDir, '4-liquefaction-response.png'), fullPage: true });
  const liqVisible1 = await page.locator('text="Explain soil liquefaction"').isVisible().catch(() => false);
  console.log(`✓ Liquefaction message visible: ${liqVisible1}`);

  console.log('\n=== STEP 5: SWITCH TO SPT THREAD (CRITICAL) ===');
  const threads = await page.locator('[class*="thread"], li').all();
  console.log(`Found ${threads.length} threads`);
  
  // Try to find SPT thread
  let sptThread = await page.locator('text*="SPT"').first();
  let found = await sptThread.isVisible().catch(() => false);
  
  if (found) {
    await sptThread.click();
    console.log('✓ Clicked SPT thread');
  } else if (threads.length > 0) {
    await threads[0].click();
    console.log('✓ Clicked first thread');
  }
  
  await page.waitForTimeout(5000);
  await page.screenshot({ path: path.join(screenshotsDir, '5-switched-to-spt.png'), fullPage: true });
  
  const sptMsgVisible = await page.locator('text="What is SPT test?"').isVisible().catch(() => false);
  const liqMsgVisible = await page.locator('text="Explain soil liquefaction"').isVisible().catch(() => false);
  
  console.log(`\n🔍 SPT message visible: ${sptMsgVisible}`);
  console.log(`🔍 Liquefaction message visible: ${liqMsgVisible}`);
  
  if (sptMsgVisible && !liqMsgVisible) {
    console.log('✅ STEP 5: PASS - Correct SPT messages loaded!');
  } else if (liqMsgVisible) {
    console.log('❌ STEP 5: FAIL - Wrong messages (liquefaction instead of SPT)');
  } else {
    console.log('❌ STEP 5: FAIL - SPT messages not loaded');
  }

  console.log('\n=== STEP 6: SWITCH TO LIQUEFACTION THREAD (CRITICAL) ===');
  
  let liqThread = await page.locator('text*="liquefaction"').first();
  found = await liqThread.isVisible().catch(() => false);
  
  if (found) {
    await liqThread.click();
    console.log('✓ Clicked liquefaction thread');
  } else if (threads.length > 1) {
    await threads[1].click();
    console.log('✓ Clicked second thread');
  }
  
  await page.waitForTimeout(5000);
  await page.screenshot({ path: path.join(screenshotsDir, '6-switched-to-liquefaction.png'), fullPage: true });
  
  const liqMsgVisible2 = await page.locator('text="Explain soil liquefaction"').isVisible().catch(() => false);
  const sptMsgVisible2 = await page.locator('text="What is SPT test?"').isVisible().catch(() => false);
  
  console.log(`\n🔍 Liquefaction message visible: ${liqMsgVisible2}`);
  console.log(`🔍 SPT message visible: ${sptMsgVisible2}`);
  
  if (liqMsgVisible2 && !sptMsgVisible2) {
    console.log('✅ STEP 6: PASS - Correct liquefaction messages loaded!');
  } else if (sptMsgVisible2) {
    console.log('❌ STEP 6: FAIL - Wrong messages (SPT instead of liquefaction)');
  } else {
    console.log('❌ STEP 6: FAIL - Liquefaction messages not loaded');
  }

  console.log('\n' + '='.repeat(60));
  console.log('FINAL VERDICT');
  console.log('='.repeat(60));
  
  if (sptMsgVisible && !liqMsgVisible && liqMsgVisible2 && !sptMsgVisible2) {
    console.log('✅✅✅ PASS - THREAD SWITCHING WORKS CORRECTLY! ✅✅✅');
  } else {
    console.log('❌ FAIL - Thread switching still broken');
  }
});
