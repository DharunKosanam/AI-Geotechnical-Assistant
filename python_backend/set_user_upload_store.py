import os, asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

async def fix():
    c = AsyncOpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    user_upload_vs = os.getenv('OPENAI_VECTOR_STORE_ID')
    assistant_id = os.getenv('OPENAI_ASSISTANT_ID')
    
    print(f"\nSetting Assistant to use USER UPLOAD Vector Store:")
    print(f"  Assistant: {assistant_id}")
    print(f"  Vector Store: {user_upload_vs}")
    
    await c.beta.assistants.update(
        assistant_id,
        tool_resources={'file_search': {'vector_store_ids': [user_upload_vs]}}
    )
    
    print(f"\nSUCCESS! Assistant now uses USER UPLOAD Vector Store")
    print(f"\nNext: Restart backend server and test!")

asyncio.run(fix())

