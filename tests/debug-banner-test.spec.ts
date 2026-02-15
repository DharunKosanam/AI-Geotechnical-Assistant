import { test } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

const screenshotsDir = path.join(__dirname, 'debug-banner-screenshots');
if (!fs.existsSync(screenshotsDir)) {
  fs.mkdirSync(screenshotsDir, { recursive: true });
}

test('Debug Banner Thread Switching Test', async ({ page }) => {
  test.setTimeout(180000);
  
  let step2ThreadId = '';
  let step2MessageCount = '';
  let step4ThreadId = '';
  let step4MessageCount = '';
  let step5ThreadId = '';
  let step5MessageCount = '';
  let step6ThreadId = '';
  let step6MessageCount = '';

  console.log('\n' + '='.repeat(80));
  console.log('THREAD SWITCHING TEST WITH DEBUG BANNER');
  console.log('='.repeat(80));

  console.log('\n=== STEP 1: NAVIGATE ===');
  await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
  await page.waitForTimeout(5000);
  await page.screenshot({ path: path.join(screenshotsDir, '1-initial-load.png'), fullPage: true });
  console.log('✓ Navigated to http://localhost:3000');

  console.log('\n=== STEP 2: SEND BEARING CAPACITY MESSAGE ===');
  const input1 = await page.locator('textarea, input[type="text"]').last();
  await input1.fill('What is bearing capacity?');
  await input1.press('Enter');
  console.log('✓ Sent: "What is bearing capacity?"');
  
  await page.waitForTimeout(20000);
  await page.screenshot({ path: path.join(screenshotsDir, '2-bearing-capacity.png'), fullPage: true });
  
  // Read debug banner
  const debugBanner2 = await page.locator('[class*="debug"], [class*="Debug"], div:has-text("Thread ID")').first().textContent().catch(() => 'Not found');
  console.log(`\n📊 DEBUG BANNER (Step 2):`);
  console.log(`   Raw text: ${debugBanner2}`);
  
  // Extract thread ID and message count
  const threadIdMatch2 = debugBanner2.match(/Thread ID[:\s]+([a-f0-9-]+)/i);
  const msgCountMatch2 = debugBanner2.match(/(\d+)\s+message/i);
  
  if (threadIdMatch2) {
    step2ThreadId = threadIdMatch2[1];
    console.log(`   Thread ID: ${step2ThreadId}`);
  }
  if (msgCountMatch2) {
    step2MessageCount = msgCountMatch2[1];
    console.log(`   Message count: ${step2MessageCount}`);
  }

  console.log('\n=== STEP 3: NEW CHAT ===');
  const newChatBtn = await page.locator('button:has-text("New Chat"), button:has-text("New")').first();
  await newChatBtn.click();
  console.log('✓ Clicked New Chat button');
  await page.waitForTimeout(3000);

  console.log('\n=== STEP 4: SEND ATTERBERG LIMITS MESSAGE ===');
  const input2 = await page.locator('textarea, input[type="text"]').last();
  await input2.fill('Explain Atterberg limits');
  await input2.press('Enter');
  console.log('✓ Sent: "Explain Atterberg limits"');
  
  await page.waitForTimeout(20000);
  await page.screenshot({ path: path.join(screenshotsDir, '3-atterberg-limits.png'), fullPage: true });
  
  // Read debug banner
  const debugBanner4 = await page.locator('[class*="debug"], [class*="Debug"], div:has-text("Thread ID")').first().textContent().catch(() => 'Not found');
  console.log(`\n📊 DEBUG BANNER (Step 4):`);
  console.log(`   Raw text: ${debugBanner4}`);
  
  const threadIdMatch4 = debugBanner4.match(/Thread ID[:\s]+([a-f0-9-]+)/i);
  const msgCountMatch4 = debugBanner4.match(/(\d+)\s+message/i);
  
  if (threadIdMatch4) {
    step4ThreadId = threadIdMatch4[1];
    console.log(`   Thread ID: ${step4ThreadId}`);
  }
  if (msgCountMatch4) {
    step4MessageCount = msgCountMatch4[1];
    console.log(`   Message count: ${step4MessageCount}`);
  }

  console.log('\n=== STEP 5: SWITCH TO BEARING CAPACITY THREAD (CRITICAL) ===');
  
  // Find threads
  const threads = await page.locator('[class*="thread"], li').all();
  console.log(`Found ${threads.length} threads in sidebar`);
  
  // Try to find bearing capacity thread
  let bearingThread = await page.locator('text*="bearing"').first();
  let found = await bearingThread.isVisible().catch(() => false);
  
  if (found) {
    console.log('✓ Found bearing capacity thread by text');
    await bearingThread.click();
  } else if (threads.length > 0) {
    console.log('✓ Clicking first thread in list');
    await threads[0].click();
  }
  
  console.log('✓ Clicked on bearing capacity thread');
  await page.waitForTimeout(5000);
  await page.screenshot({ path: path.join(screenshotsDir, '4-switched-to-bearing.png'), fullPage: true });
  
  // Read debug banner
  const debugBanner5 = await page.locator('[class*="debug"], [class*="Debug"], div:has-text("Thread ID")').first().textContent().catch(() => 'Not found');
  console.log(`\n📊 DEBUG BANNER (Step 5):`);
  console.log(`   Raw text: ${debugBanner5}`);
  
  const threadIdMatch5 = debugBanner5.match(/Thread ID[:\s]+([a-f0-9-]+)/i);
  const msgCountMatch5 = debugBanner5.match(/(\d+)\s+message/i);
  
  if (threadIdMatch5) {
    step5ThreadId = threadIdMatch5[1];
    console.log(`   Thread ID: ${step5ThreadId}`);
  }
  if (msgCountMatch5) {
    step5MessageCount = msgCountMatch5[1];
    console.log(`   Message count: ${step5MessageCount}`);
  }
  
  // Check messages
  const bearingMsg = await page.locator('text="What is bearing capacity?"').isVisible().catch(() => false);
  const atterbergMsg = await page.locator('text="Explain Atterberg limits"').isVisible().catch(() => false);
  
  console.log(`\n🔍 MESSAGES VISIBLE:`);
  console.log(`   "What is bearing capacity?" visible: ${bearingMsg}`);
  console.log(`   "Explain Atterberg limits" visible: ${atterbergMsg}`);
  
  console.log(`\n🔍 THREAD ID COMPARISON:`);
  console.log(`   Step 2 (created): ${step2ThreadId}`);
  console.log(`   Step 5 (loaded):  ${step5ThreadId}`);
  console.log(`   Match: ${step2ThreadId === step5ThreadId ? '✅ YES' : '❌ NO'}`);
  
  if (step2ThreadId === step5ThreadId && bearingMsg && !atterbergMsg) {
    console.log('\n✅ STEP 5: PASS - Correct thread loaded with correct messages!');
  } else if (step2ThreadId !== step5ThreadId) {
    console.log('\n❌ STEP 5: FAIL - Thread ID mismatch!');
  } else if (!bearingMsg || atterbergMsg) {
    console.log('\n❌ STEP 5: FAIL - Wrong messages displayed!');
  }

  console.log('\n=== STEP 6: SWITCH TO ATTERBERG LIMITS THREAD (CRITICAL) ===');
  
  let atterbergThread = await page.locator('text*="Atterberg"').first();
  found = await atterbergThread.isVisible().catch(() => false);
  
  if (found) {
    console.log('✓ Found Atterberg thread by text');
    await atterbergThread.click();
  } else if (threads.length > 1) {
    console.log('✓ Clicking second thread in list');
    await threads[1].click();
  }
  
  console.log('✓ Clicked on Atterberg thread');
  await page.waitForTimeout(5000);
  await page.screenshot({ path: path.join(screenshotsDir, '5-switched-to-atterberg.png'), fullPage: true });
  
  // Read debug banner
  const debugBanner6 = await page.locator('[class*="debug"], [class*="Debug"], div:has-text("Thread ID")').first().textContent().catch(() => false);
  console.log(`\n📊 DEBUG BANNER (Step 6):`);
  console.log(`   Raw text: ${debugBanner6}`);
  
  const threadIdMatch6 = debugBanner6.match(/Thread ID[:\s]+([a-f0-9-]+)/i);
  const msgCountMatch6 = debugBanner6.match(/(\d+)\s+message/i);
  
  if (threadIdMatch6) {
    step6ThreadId = threadIdMatch6[1];
    console.log(`   Thread ID: ${step6ThreadId}`);
  }
  if (msgCountMatch6) {
    step6MessageCount = msgCountMatch6[1];
    console.log(`   Message count: ${step6MessageCount}`);
  }
  
  // Check messages
  const atterbergMsg2 = await page.locator('text="Explain Atterberg limits"').isVisible().catch(() => false);
  const bearingMsg2 = await page.locator('text="What is bearing capacity?"').isVisible().catch(() => false);
  
  console.log(`\n🔍 MESSAGES VISIBLE:`);
  console.log(`   "Explain Atterberg limits" visible: ${atterbergMsg2}`);
  console.log(`   "What is bearing capacity?" visible: ${bearingMsg2}`);
  
  console.log(`\n🔍 THREAD ID COMPARISON:`);
  console.log(`   Step 4 (created): ${step4ThreadId}`);
  console.log(`   Step 6 (loaded):  ${step6ThreadId}`);
  console.log(`   Match: ${step4ThreadId === step6ThreadId ? '✅ YES' : '❌ NO'}`);
  
  if (step4ThreadId === step6ThreadId && atterbergMsg2 && !bearingMsg2) {
    console.log('\n✅ STEP 6: PASS - Correct thread loaded with correct messages!');
  } else if (step4ThreadId !== step6ThreadId) {
    console.log('\n❌ STEP 6: FAIL - Thread ID mismatch!');
  } else if (!atterbergMsg2 || bearingMsg2) {
    console.log('\n❌ STEP 6: FAIL - Wrong messages displayed!');
  }

  console.log('\n' + '='.repeat(80));
  console.log('FINAL SUMMARY');
  console.log('='.repeat(80));
  
  console.log('\n📋 THREAD IDs:');
  console.log(`   Bearing Capacity (Step 2): ${step2ThreadId} (${step2MessageCount} msgs)`);
  console.log(`   Atterberg Limits (Step 4): ${step4ThreadId} (${step4MessageCount} msgs)`);
  console.log(`   After Switch 1   (Step 5): ${step5ThreadId} (${step5MessageCount} msgs)`);
  console.log(`   After Switch 2   (Step 6): ${step6ThreadId} (${step6MessageCount} msgs)`);
  
  console.log('\n🔍 ID MATCHES:');
  console.log(`   Bearing: ${step2ThreadId} === ${step5ThreadId} ? ${step2ThreadId === step5ThreadId ? '✅' : '❌'}`);
  console.log(`   Atterberg: ${step4ThreadId} === ${step6ThreadId} ? ${step4ThreadId === step6ThreadId ? '✅' : '❌'}`);
  
  console.log('\n🔍 MESSAGE CHECKS:');
  console.log(`   Step 5 showed bearing capacity: ${bearingMsg ? '✅' : '❌'}`);
  console.log(`   Step 6 showed Atterberg limits: ${atterbergMsg2 ? '✅' : '❌'}`);
  
  const allPass = 
    step2ThreadId === step5ThreadId && 
    step4ThreadId === step6ThreadId && 
    bearingMsg && 
    !atterbergMsg && 
    atterbergMsg2 && 
    !bearingMsg2;
  
  console.log('\n' + '='.repeat(80));
  if (allPass) {
    console.log('✅✅✅ FINAL VERDICT: PASS - THREAD SWITCHING WORKS! ✅✅✅');
  } else {
    console.log('❌ FINAL VERDICT: FAIL - THREAD SWITCHING BROKEN');
  }
  console.log('='.repeat(80));
});
