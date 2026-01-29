"""
Configure Assistant to use BOTH Vector Stores:
1. User Upload Store (for dynamically uploaded files)
2. Knowledge Base Store (for pre-existing knowledge)
"""
import os
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

async def setup_dual_stores():
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
    user_upload_vs = os.getenv("OPENAI_VECTOR_STORE_ID")
    knowledge_base_vs = os.getenv("OPENAI_KNOWLEDGE_STORE_ID")
    
    print("\n" + "="*70)
    print("CONFIGURING ASSISTANT WITH DUAL VECTOR STORES")
    print("="*70)
    
    print(f"\nAssistant ID: {assistant_id}")
    print(f"\nVector Stores:")
    print(f"  1. User Uploads:   {user_upload_vs}")
    print(f"  2. Knowledge Base: {knowledge_base_vs}")
    
    print(f"\nUpdating Assistant to use BOTH Vector Stores...")
    
    try:
        # Update Assistant to use both Vector Stores
        updated_assistant = await client.beta.assistants.update(
            assistant_id=assistant_id,
            tool_resources={
                "file_search": {
                    "vector_store_ids": [user_upload_vs, knowledge_base_vs]
                }
            }
        )
        
        print(f"\nSUCCESS! Assistant updated!")
        
        # Verify
        print(f"\nVerifying configuration...")
        assistant = await client.beta.assistants.retrieve(assistant_id)
        
        vs_ids = []
        if hasattr(assistant, 'tool_resources') and assistant.tool_resources:
            if hasattr(assistant.tool_resources, 'file_search'):
                if hasattr(assistant.tool_resources.file_search, 'vector_store_ids'):
                    vs_ids = assistant.tool_resources.file_search.vector_store_ids
        
        print(f"\nAssistant is now using {len(vs_ids)} Vector Store(s):")
        for i, vs_id in enumerate(vs_ids, 1):
            if vs_id == user_upload_vs:
                print(f"  {i}. {vs_id} (User Uploads)")
            elif vs_id == knowledge_base_vs:
                print(f"  {i}. {vs_id} (Knowledge Base)")
            else:
                print(f"  {i}. {vs_id} (Unknown)")
        
        if user_upload_vs in vs_ids and knowledge_base_vs in vs_ids:
            print(f"\n✅ PERFECT! Assistant can now search:")
            print(f"   - User-uploaded files (dynamic)")
            print(f"   - Knowledge base files (pre-existing)")
            print(f"\n📝 Next steps:")
            print(f"   1. Restart the backend server")
            print(f"   2. Test multi-file upload")
            print(f"   3. AI will search BOTH stores automatically!")
        else:
            print(f"\n⚠️  WARNING: Not all Vector Stores are configured")
            if user_upload_vs not in vs_ids:
                print(f"   Missing: User Upload Store")
            if knowledge_base_vs not in vs_ids:
                print(f"   Missing: Knowledge Base Store")
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(setup_dual_stores())

