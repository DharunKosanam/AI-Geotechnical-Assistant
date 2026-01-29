"""
Fix Vector Store linkage between Assistant and .env configuration
"""
import os
import asyncio
from dotenv import load_dotenv, set_key
from openai import AsyncOpenAI

load_dotenv()

async def fix_vector_store():
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
    env_vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID")
    
    print("\n" + "="*70)
    print("🔧 FIXING VECTOR STORE LINKAGE")
    print("="*70)
    
    # Get Assistant's current Vector Store
    assistant = await client.beta.assistants.retrieve(assistant_id)
    
    assistant_vs_ids = []
    if hasattr(assistant, 'tool_resources') and assistant.tool_resources:
        if hasattr(assistant.tool_resources, 'file_search'):
            if hasattr(assistant.tool_resources.file_search, 'vector_store_ids'):
                assistant_vs_ids = assistant.tool_resources.file_search.vector_store_ids
    
    print(f"\n📋 Current Configuration:")
    print(f"   Assistant ID: {assistant_id}")
    print(f"   .env Vector Store: {env_vector_store_id}")
    print(f"   Assistant's Vector Store(s): {assistant_vs_ids}")
    
    if not assistant_vs_ids:
        print(f"\n❌ Assistant has NO Vector Stores linked!")
        print(f"\n💡 SOLUTION: Link the .env Vector Store to the Assistant")
        
        print(f"\nUpdating Assistant to use Vector Store: {env_vector_store_id}")
        
        # Update assistant to use the .env vector store
        updated_assistant = await client.beta.assistants.update(
            assistant_id=assistant_id,
            tool_resources={
                "file_search": {
                    "vector_store_ids": [env_vector_store_id]
                }
            }
        )
        
        print(f"✅ Assistant updated successfully!")
        print(f"   Now using Vector Store: {env_vector_store_id}")
        
    elif env_vector_store_id in assistant_vs_ids:
        print(f"\n✅ Configuration is CORRECT!")
        print(f"   Assistant is already using the .env Vector Store")
        
    else:
        print(f"\n⚠️  MISMATCH DETECTED!")
        print(f"   .env points to: {env_vector_store_id}")
        print(f"   Assistant uses: {assistant_vs_ids[0]}")
        
        print(f"\n🤔 Which Vector Store should we use?")
        print(f"\n   Option 1: Use Assistant's current Vector Store (RECOMMENDED)")
        print(f"             This preserves any existing files in the Assistant's store")
        print(f"   Option 2: Update Assistant to use .env Vector Store")
        print(f"             This switches to the .env store (may be empty)")
        
        choice = input(f"\nEnter 1 or 2 (or press Enter for Option 1): ").strip() or "1"
        
        if choice == "1":
            # Update .env to match Assistant
            new_vs_id = assistant_vs_ids[0]
            print(f"\n📝 Updating .env file to use: {new_vs_id}")
            
            env_path = os.path.join(os.path.dirname(__file__), '.env')
            set_key(env_path, 'OPENAI_VECTOR_STORE_ID', new_vs_id)
            
            print(f"✅ .env file updated!")
            print(f"   OPENAI_VECTOR_STORE_ID = {new_vs_id}")
            
        else:
            # Update Assistant to use .env vector store
            print(f"\n📝 Updating Assistant to use: {env_vector_store_id}")
            
            updated_assistant = await client.beta.assistants.update(
                assistant_id=assistant_id,
                tool_resources={
                    "file_search": {
                        "vector_store_ids": [env_vector_store_id]
                    }
                }
            )
            
            print(f"✅ Assistant updated!")
            print(f"   Now using Vector Store: {env_vector_store_id}")
    
    # Verify the fix
    print(f"\n🔍 Verifying configuration...")
    assistant = await client.beta.assistants.retrieve(assistant_id)
    
    final_vs_ids = []
    if hasattr(assistant, 'tool_resources') and assistant.tool_resources:
        if hasattr(assistant.tool_resources, 'file_search'):
            if hasattr(assistant.tool_resources.file_search, 'vector_store_ids'):
                final_vs_ids = assistant.tool_resources.file_search.vector_store_ids
    
    # Reload .env to get updated value
    load_dotenv(override=True)
    final_env_vs_id = os.getenv("OPENAI_VECTOR_STORE_ID")
    
    print(f"\n✅ Final Configuration:")
    print(f"   .env Vector Store: {final_env_vs_id}")
    print(f"   Assistant's Vector Store: {final_vs_ids[0] if final_vs_ids else 'None'}")
    
    if final_vs_ids and final_env_vs_id == final_vs_ids[0]:
        print(f"\n🎉 SUCCESS! Configuration is now synchronized!")
        print(f"\n📝 Next steps:")
        print(f"   1. ⚠️  RESTART the backend server (IMPORTANT!)")
        print(f"   2. Upload a new file")
        print(f"   3. Check logs for: '✅ Attached file to Vector Store'")
        print(f"   4. Test multi-file scenario")
    else:
        print(f"\n⚠️  Configuration still mismatched. Please run this script again.")
    
    print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(fix_vector_store())

