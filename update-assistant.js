#!/usr/bin/env node

/**
 * One-time script to update your existing Assistant with enhanced file search capabilities
 * 
 * Usage: node update-assistant.js
 * 
 * This will:
 * 1. Update your assistant's instructions to force comprehensive file searches
 * 2. Configure file_search to retrieve up to 50 chunks (vs default 20)
 * 3. Add ranking options for better relevance filtering
 */

const PORT = process.env.PORT || 3000;
const UPDATE_URL = `http://localhost:${PORT}/api/assistants/update`;

console.log('\n🔧 Updating Assistant Configuration...\n');
console.log(`Sending POST request to: ${UPDATE_URL}\n`);

fetch(UPDATE_URL, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
  },
})
  .then(async (response) => {
    const data = await response.json();
    
    if (response.ok) {
      console.log('✅ SUCCESS! Assistant updated successfully!\n');
      console.log('📋 Assistant Details:');
      console.log('  - ID:', data.assistantId);
      console.log('  - Name:', data.name);
      console.log('  - Model:', data.model);
      console.log('  - Tools:', data.tools.map(t => t.type).join(', '));
      console.log('\n💡', data.message);
      console.log('\n🎉 Your assistant is now configured for enhanced RAG capabilities!');
      console.log('   It will search up to 50 chunks and cross-reference all files.\n');
    } else {
      console.error('❌ ERROR: Failed to update assistant\n');
      console.error('Response:', data);
      console.error('\n💡 Make sure your dev server is running: npm run dev\n');
      process.exit(1);
    }
  })
  .catch((error) => {
    console.error('❌ ERROR: Could not connect to server\n');
    console.error(error.message);
    console.error('\n💡 Make sure your dev server is running on port', PORT);
    console.error('   Run: npm run dev\n');
    process.exit(1);
  });

