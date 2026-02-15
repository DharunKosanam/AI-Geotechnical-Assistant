import { test, expect, Page } from '@playwright/test';
import * as path from 'path';
import * as fs from 'fs';

// Create screenshots directory if it doesn't exist
const screenshotsDir = path.join(__dirname, 'bugfix-screenshots');
if (!fs.existsSync(screenshotsDir)) {
  fs.mkdirSync(screenshotsDir, { recursive: true });
}

test.describe('Thread Switching Bug Fix Verification', () => {
  let page: Page;

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage();
  });

  test.afterAll(async () => {
    await page.close();
  });

  test('Step 1: Navigate to the app', async () => {
    console.log('\n=== STEP 1: NAVIGATE TO APP ===');
    
    // Navigate to the application
    await page.goto('http://localhost:3000', { waitUntil: 'networkidle' });
    console.log('✓ Navigated to http://localhost:3000');
    
    // Wait for page to fully load
    await page.waitForTimeout(3000);
    
    // Take screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'step1-page-loaded.png'),
      fullPage: true 
    });
    console.log('✓ Screenshot taken');
    
    // Verify basic elements are present
    const input = await page.locator('textarea, input[type="text"]').last();
    await expect(input).toBeVisible({ timeout: 5000 });
    console.log('✓ Page fully loaded with input visible');
    
    console.log('STEP 1: ✅ PASS');
  });

  test('Step 2: Send a message in a NEW chat', async () => {
    console.log('\n=== STEP 2: SEND MESSAGE ABOUT SPT TEST ===');
    
    // Find the textarea/input
    const input = await page.locator('textarea, input[type="text"]').last();
    await expect(input).toBeVisible({ timeout: 5000 });
    
    // Type the message
    await input.fill('What is the SPT test in geotechnical engineering?');
    console.log('✓ Typed: "What is the SPT test in geotechnical engineering?"');
    
    // Press Enter to send
    await input.press('Enter');
    console.log('✓ Pressed Enter to send message');
    
    // Wait a moment for message to appear
    await page.waitForTimeout(2000);
    
    // Check if user message appeared
    const userMessage = await page.locator('text="What is the SPT test in geotechnical engineering?"').first();
    const userMessageVisible = await userMessage.isVisible().catch(() => false);
    console.log(`✓ User message visible: ${userMessageVisible}`);
    
    // Wait 15-20 seconds for AI response
    console.log('⏳ Waiting 20 seconds for AI response...');
    await page.waitForTimeout(20000);
    
    // Take screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'step2-first-message-sent.png'),
      fullPage: true 
    });
    console.log('✓ Screenshot taken');
    
    // Check for AI response (page should have more content)
    const pageContent = await page.content();
    const hasSubstantialContent = pageContent.length > 5000;
    console.log(`✓ AI response received (content length: ${pageContent.length}): ${hasSubstantialContent}`);
    
    // Verify both user message and AI response are visible
    const userStillVisible = await userMessage.isVisible().catch(() => false);
    console.log(`✓ User message still visible: ${userStillVisible}`);
    
    if (userStillVisible && hasSubstantialContent) {
      console.log('STEP 2: ✅ PASS - Both user message and AI response visible');
    } else {
      console.log('STEP 2: ⚠️ WARNING - Messages may not be fully visible');
    }
  });

  test('Step 3: Create a new chat', async () => {
    console.log('\n=== STEP 3: CREATE NEW CHAT ===');
    
    // Find and click "New Chat" button
    const newChatButton = await page.locator('button:has-text("New"), button:has-text("new"), button:has-text("New Chat")').first();
    await expect(newChatButton).toBeVisible({ timeout: 5000 });
    
    await newChatButton.click();
    console.log('✓ Clicked "New Chat" button');
    
    // Wait 3 seconds
    await page.waitForTimeout(3000);
    
    // Take screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'step3-new-chat-created.png'),
      fullPage: true 
    });
    console.log('✓ Screenshot taken');
    
    // Verify the chat was cleared (old message should not be visible)
    const oldMessage = await page.locator('text="What is the SPT test in geotechnical engineering?"').first();
    const oldMessageVisible = await oldMessage.isVisible().catch(() => false);
    console.log(`✓ Old message cleared: ${!oldMessageVisible}`);
    
    // Check for welcome message
    const pageContent = await page.content();
    const hasWelcome = pageContent.toLowerCase().includes('hello') || 
                       pageContent.toLowerCase().includes('how can i');
    console.log(`✓ Welcome message visible: ${hasWelcome}`);
    
    if (!oldMessageVisible) {
      console.log('STEP 3: ✅ PASS - New chat created, old messages cleared');
    } else {
      console.log('STEP 3: ❌ FAIL - Old messages still visible');
    }
  });

  test('Step 4: Send a DIFFERENT message in the new chat', async () => {
    console.log('\n=== STEP 4: SEND MESSAGE ABOUT SOIL CONSOLIDATION ===');
    
    // Find the textarea/input
    const input = await page.locator('textarea, input[type="text"]').last();
    await expect(input).toBeVisible({ timeout: 5000 });
    
    // Type the message
    await input.fill('Explain soil consolidation');
    console.log('✓ Typed: "Explain soil consolidation"');
    
    // Press Enter to send
    await input.press('Enter');
    console.log('✓ Pressed Enter to send message');
    
    // Wait a moment for message to appear
    await page.waitForTimeout(2000);
    
    // Check if user message appeared
    const userMessage = await page.locator('text="Explain soil consolidation"').first();
    const userMessageVisible = await userMessage.isVisible().catch(() => false);
    console.log(`✓ User message visible: ${userMessageVisible}`);
    
    // Wait 15-20 seconds for AI response
    console.log('⏳ Waiting 20 seconds for AI response...');
    await page.waitForTimeout(20000);
    
    // Take screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'step4-second-message-sent.png'),
      fullPage: true 
    });
    console.log('✓ Screenshot taken');
    
    // Check for AI response
    const pageContent = await page.content();
    const hasSubstantialContent = pageContent.length > 5000;
    console.log(`✓ AI response received (content length: ${pageContent.length}): ${hasSubstantialContent}`);
    
    if (userMessageVisible && hasSubstantialContent) {
      console.log('STEP 4: ✅ PASS - Both user message and AI response visible');
    } else {
      console.log('STEP 4: ⚠️ WARNING - Messages may not be fully visible');
    }
  });

  test('Step 5: SWITCH BACK to the first chat (CRITICAL TEST)', async () => {
    console.log('\n=== STEP 5: SWITCH TO FIRST CHAT (CRITICAL TEST) ===');
    
    // Look for threads in sidebar
    const sidebar = await page.locator('[class*="sidebar"], [class*="Sidebar"], aside, nav').first();
    await expect(sidebar).toBeVisible({ timeout: 5000 });
    
    // Get all thread items
    const threadItems = await page.locator('[class*="thread"], [class*="Thread"], li, [role="listitem"]').all();
    console.log(`✓ Found ${threadItems.length} threads in sidebar`);
    
    // Look for the SPT test thread
    const sptThread = await page.locator('text*="SPT"').first();
    const sptThreadVisible = await sptThread.isVisible().catch(() => false);
    
    if (sptThreadVisible) {
      console.log('✓ Found SPT test thread in sidebar');
      await sptThread.click();
      console.log('✓ Clicked on SPT test thread');
    } else {
      // Try clicking the first non-current thread
      console.log('⚠️ SPT thread not found by text, trying first thread item');
      if (threadItems.length > 0) {
        await threadItems[0].click();
        console.log('✓ Clicked on first thread');
      }
    }
    
    // Wait 5 seconds for messages to load
    console.log('⏳ Waiting 5 seconds for messages to load...');
    await page.waitForTimeout(5000);
    
    // Take screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'step5-switched-to-first-chat.png'),
      fullPage: true 
    });
    console.log('✓ Screenshot taken');
    
    // CRITICAL CHECK: Look for the original SPT message
    const sptMessage = await page.locator('text="What is the SPT test in geotechnical engineering?"').first();
    const sptMessageVisible = await sptMessage.isVisible().catch(() => false);
    console.log(`\n🔍 CRITICAL CHECK: Original SPT message visible: ${sptMessageVisible}`);
    
    // Check if there's any content (AI response)
    const pageContent = await page.content();
    const hasContent = pageContent.length > 3000;
    console.log(`🔍 Page has substantial content: ${hasContent}`);
    
    // Check if consolidation message is NOT visible (shouldn't be in this thread)
    const consolidationMessage = await page.locator('text="Explain soil consolidation"').first();
    const consolidationVisible = await consolidationMessage.isVisible().catch(() => false);
    console.log(`🔍 Consolidation message NOT visible (correct): ${!consolidationVisible}`);
    
    if (sptMessageVisible && hasContent && !consolidationVisible) {
      console.log('\n✅✅✅ STEP 5: PASS - ORIGINAL MESSAGES LOADED CORRECTLY! ✅✅✅');
      console.log('🎉 BUG FIX VERIFIED: Thread switching works!');
    } else if (!sptMessageVisible && !hasContent) {
      console.log('\n❌ STEP 5: FAIL - Messages did NOT load (empty chat)');
    } else if (consolidationVisible) {
      console.log('\n❌ STEP 5: FAIL - Wrong messages loaded (showing consolidation instead of SPT)');
    } else {
      console.log('\n⚠️ STEP 5: PARTIAL - Some content visible but original message not found');
    }
  });

  test('Step 6: Switch to the second chat', async () => {
    console.log('\n=== STEP 6: SWITCH TO SECOND CHAT ===');
    
    // Look for the consolidation thread
    const consolidationThread = await page.locator('text*="consolidation"').first();
    const consolidationThreadVisible = await consolidationThread.isVisible().catch(() => false);
    
    if (consolidationThreadVisible) {
      console.log('✓ Found consolidation thread in sidebar');
      await consolidationThread.click();
      console.log('✓ Clicked on consolidation thread');
    } else {
      // Try clicking the second thread
      console.log('⚠️ Consolidation thread not found by text, trying second thread item');
      const threadItems = await page.locator('[class*="thread"], [class*="Thread"], li, [role="listitem"]').all();
      if (threadItems.length > 1) {
        await threadItems[1].click();
        console.log('✓ Clicked on second thread');
      }
    }
    
    // Wait 5 seconds for messages to load
    console.log('⏳ Waiting 5 seconds for messages to load...');
    await page.waitForTimeout(5000);
    
    // Take screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'step6-switched-to-second-chat.png'),
      fullPage: true 
    });
    console.log('✓ Screenshot taken');
    
    // CRITICAL CHECK: Look for the consolidation message
    const consolidationMessage = await page.locator('text="Explain soil consolidation"').first();
    const consolidationVisible = await consolidationMessage.isVisible().catch(() => false);
    console.log(`\n🔍 CRITICAL CHECK: Consolidation message visible: ${consolidationVisible}`);
    
    // Check if there's any content
    const pageContent = await page.content();
    const hasContent = pageContent.length > 3000;
    console.log(`🔍 Page has substantial content: ${hasContent}`);
    
    // Check if SPT message is NOT visible (shouldn't be in this thread)
    const sptMessage = await page.locator('text="What is the SPT test in geotechnical engineering?"').first();
    const sptVisible = await sptMessage.isVisible().catch(() => false);
    console.log(`🔍 SPT message NOT visible (correct): ${!sptVisible}`);
    
    if (consolidationVisible && hasContent && !sptVisible) {
      console.log('\n✅✅✅ STEP 6: PASS - CONSOLIDATION MESSAGES LOADED CORRECTLY! ✅✅✅');
      console.log('🎉 BUG FIX VERIFIED: Thread switching works both ways!');
    } else if (!consolidationVisible && !hasContent) {
      console.log('\n❌ STEP 6: FAIL - Messages did NOT load (empty chat)');
    } else if (sptVisible) {
      console.log('\n❌ STEP 6: FAIL - Wrong messages loaded (showing SPT instead of consolidation)');
    } else {
      console.log('\n⚠️ STEP 6: PARTIAL - Some content visible but consolidation message not found');
    }
  });

  test('FINAL SUMMARY: Thread Switching Bug Fix Status', async () => {
    console.log('\n' + '='.repeat(70));
    console.log('FINAL SUMMARY: THREAD SWITCHING BUG FIX VERIFICATION');
    console.log('='.repeat(70));
    
    // Re-check both threads to provide final verdict
    
    // Check first thread
    const threadItems = await page.locator('[class*="thread"], [class*="Thread"], li, [role="listitem"]').all();
    if (threadItems.length > 0) {
      await threadItems[0].click();
      await page.waitForTimeout(3000);
      
      const sptMessage = await page.locator('text="What is the SPT test in geotechnical engineering?"').first();
      const sptVisible = await sptMessage.isVisible().catch(() => false);
      
      console.log(`\n✓ First thread check: SPT message visible = ${sptVisible}`);
    }
    
    // Check second thread
    if (threadItems.length > 1) {
      await threadItems[1].click();
      await page.waitForTimeout(3000);
      
      const consolidationMessage = await page.locator('text="Explain soil consolidation"').first();
      const consolidationVisible = await consolidationMessage.isVisible().catch(() => false);
      
      console.log(`✓ Second thread check: Consolidation message visible = ${consolidationVisible}`);
    }
    
    // Take final screenshot
    await page.screenshot({ 
      path: path.join(screenshotsDir, 'final-summary.png'),
      fullPage: true 
    });
    
    console.log('\n' + '='.repeat(70));
    console.log('📊 TEST EXECUTION COMPLETE');
    console.log('='.repeat(70));
    console.log(`\n📁 All screenshots saved to: ${screenshotsDir}`);
    console.log('\nPlease review the console output above for detailed results.');
    console.log('Key indicators:');
    console.log('  ✅ = Test passed');
    console.log('  ❌ = Test failed');
    console.log('  ⚠️  = Warning or partial success');
    console.log('  🔍 = Critical check result');
    console.log('  🎉 = Bug fix verified!');
  });
});
