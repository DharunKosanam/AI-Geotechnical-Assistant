"""
Quick script to check Vector Store configuration and Assistant setup
"""
import os
import asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()

async def check_setup():
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    
    assistant_id = os.getenv("OPENAI_ASSISTANT_ID")
    vector_store_id = os.getenv("OPENAI_VECTOR_STORE_ID")
    
    print("\n" + "="*60)
    print("🔍 CHECKING VECTOR STORE CONFIGURATION")
    print("="*60)
    
    # Check environment variables
    print(f"\n✅ ASSISTANT_ID: {assistant_id}")
    print(f"✅ VECTOR_STORE_ID: {vector_store_id}")
    
    # Check Assistant configuration
    print(f"\n📋 Checking Assistant configuration...")
    try:
        assistant = await client.beta.assistants.retrieve(assistant_id)
        print(f"   Name: {assistant.name}")
        print(f"   Model: {assistant.model}")
        print(f"   Tools: {[tool.type for tool in assistant.tools]}")
        
        # Check if file_search tool is enabled
        has_file_search = any(tool.type == "file_search" for tool in assistant.tools)
        if has_file_search:
            print(f"   ✅ file_search tool is ENABLED")
        else:
            print(f"   ❌ file_search tool is NOT ENABLED")
            print(f"      → This is the problem! Assistant needs file_search tool.")
        
        # Check tool resources
        if hasattr(assistant, 'tool_resources') and assistant.tool_resources:
            print(f"\n📦 Tool Resources:")
            if hasattr(assistant.tool_resources, 'file_search'):
                vs_ids = assistant.tool_resources.file_search.vector_store_ids if hasattr(assistant.tool_resources.file_search, 'vector_store_ids') else []
                if vs_ids:
                    print(f"   Vector Stores: {vs_ids}")
                    if vector_store_id in vs_ids:
                        print(f"   ✅ Assistant is linked to Vector Store {vector_store_id}")
                    else:
                        print(f"   ⚠️  Assistant is NOT linked to Vector Store {vector_store_id}")
                        print(f"      → This might be the problem!")
                else:
                    print(f"   ⚠️  No Vector Stores linked to Assistant")
                    print(f"      → This is the problem! Need to link Vector Store.")
            else:
                print(f"   ⚠️  No file_search resources configured")
        else:
            print(f"\n⚠️  No tool_resources found on Assistant")
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Check Vector Store contents
    print(f"\n📁 Checking Vector Store contents...")
    try:
        vs_files = await client.beta.vector_stores.files.list(
            vector_store_id=vector_store_id,
            limit=100
        )
        
        if vs_files.data:
            print(f"   Found {len(vs_files.data)} files in Vector Store:")
            for i, file in enumerate(vs_files.data[:10], 1):  # Show first 10
                try:
                    file_info = await client.files.retrieve(file.id)
                    print(f"   {i}. {file_info.filename} (Status: {file.status})")
                except:
                    print(f"   {i}. {file.id} (Status: {file.status})")
            
            if len(vs_files.data) > 10:
                print(f"   ... and {len(vs_files.data) - 10} more files")
        else:
            print(f"   ⚠️  Vector Store is EMPTY")
            print(f"      → No files have been uploaded yet, or they weren't attached.")
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    # Check user uploaded files in MongoDB
    print(f"\n💾 Checking MongoDB for user uploads...")
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_uri = os.getenv("MONGODB_URI")
        mongo_client = AsyncIOMotorClient(mongo_uri)
        db = mongo_client["ai-geotech-db"]
        files_collection = db["files"]
        
        cursor = files_collection.find({"userId": "default-user", "category": "user_upload"})
        files = []
        async for doc in cursor:
            files.append(doc)
        
        if files:
            print(f"   Found {len(files)} user-uploaded files in MongoDB:")
            for i, file in enumerate(files[:10], 1):
                print(f"   {i}. {file.get('filename')} (ID: {file.get('fileId')})")
            
            if len(files) > 10:
                print(f"   ... and {len(files) - 10} more files")
        else:
            print(f"   ⚠️  No user uploads found in MongoDB")
            
        mongo_client.close()
        
    except Exception as e:
        print(f"   ⚠️  Could not check MongoDB: {e}")
    
    print("\n" + "="*60)
    print("🎯 DIAGNOSIS")
    print("="*60)
    
    # Provide diagnosis
    if not has_file_search:
        print("\n❌ PROBLEM FOUND: Assistant doesn't have file_search tool enabled")
        print("\n💡 SOLUTION:")
        print("   Run this command to enable file_search:")
        print(f"""
   from openai import OpenAI
   client = OpenAI()
   
   assistant = client.beta.assistants.update(
       assistant_id="{assistant_id}",
       tools=[{{"type": "file_search"}}],
       tool_resources={{
           "file_search": {{
               "vector_store_ids": ["{vector_store_id}"]
           }}
       }}
   )
   print("✅ Assistant updated!")
""")
    else:
        print("\n✅ Assistant configuration looks good!")
        print("\n📝 Next steps:")
        print("   1. Restart the backend server")
        print("   2. Upload a file and check for these logs:")
        print("      - '✅ Uploaded file: ...'")
        print("      - '✅ Attached file to Vector Store: ...'")
        print("   3. Ask a question about the file")
    
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(check_setup())

