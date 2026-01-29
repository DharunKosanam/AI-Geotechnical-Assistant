"""
Update Assistant to use the Vector Store ID from .env file
"""
import os
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

async def update_assistant():
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
    vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID")
    
    print("\n" + "="*70)
    print("Updating Assistant to use Vector Store from .env")
    print("="*70)
    
    print(f"\nAssistant ID: {assistant_id}")
    print(f"Vector Store ID: {vector_store_id}")
    
    print(f"\nUpdating Assistant...")
    
    try:
        updated_assistant = await client.beta.assistants.update(
            assistant_id=assistant_id,
            tool_resources={
                "file_search": {
                    "vector_store_ids": [vector_store_id]
                }
            }
        )
        
        print(f"SUCCESS! Assistant updated to use Vector Store: {vector_store_id}")
        
        # Verify
        print(f"\nVerifying...")
        assistant = await client.beta.assistants.retrieve(assistant_id)
        
        vs_ids = []
        if hasattr(assistant, 'tool_resources') and assistant.tool_resources:
            if hasattr(assistant.tool_resources, 'file_search'):
                if hasattr(assistant.tool_resources.file_search, 'vector_store_ids'):
                    vs_ids = assistant.tool_resources.file_search.vector_store_ids
        
        if vs_ids and vs_ids[0] == vector_store_id:
            print(f"VERIFIED! Assistant is now using: {vs_ids[0]}")
            print(f"\nNext steps:")
            print(f"  1. Restart the backend server")
            print(f"  2. Test multi-file upload")
            print(f"  3. Everything should work now!")
        else:
            print(f"WARNING: Verification failed")
            print(f"  Expected: {vector_store_id}")
            print(f"  Got: {vs_ids[0] if vs_ids else 'None'}")
        
    except Exception as e:
        print(f"ERROR: {e}")
    
    print("\n" + "="*70 + "\n")

if __name__ == "__main__":
    asyncio.run(update_assistant())

