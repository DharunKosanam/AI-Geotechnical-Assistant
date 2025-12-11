# Quick Setup Guide

## Prerequisites

- Python 3.8 or higher
- pip (Python package installer)
- MongoDB Atlas account or local MongoDB instance
- OpenAI API key and Assistant ID

## Step-by-Step Setup

### 1. Install Python Dependencies

#### Option A: Using the startup script (Recommended)

**On Windows:**
```bash
start.bat
```

**On macOS/Linux:**
```bash
chmod +x start.sh
./start.sh
```

The startup scripts will automatically:
- Create a virtual environment
- Install all dependencies
- Check for `.env` file
- Start the server

#### Option B: Manual installation

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

1. Copy `env.example` to `.env`:
   ```bash
   # On Windows:
   copy env.example .env
   
   # On macOS/Linux:
   cp env.example .env
   ```

2. Open `.env` and fill in your actual values:

   ```env
   # Required
   OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxx
   OPENAI_ASSISTANT_ID=asst_xxxxxxxxxxxxxxxxx
   MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority
   
   # Optional (if you use vector stores)
   OPENAI_VECTOR_STORE_ID=vs_xxxxxxxxxxxxxxxxx
   OPENAI_KNOWLEDGE_STORE_ID=vs_xxxxxxxxxxxxxxxxx
   ```

   **Where to find these values:**
   - `OPENAI_API_KEY`: OpenAI Dashboard > API Keys
   - `OPENAI_ASSISTANT_ID`: OpenAI Dashboard > Assistants
   - `MONGODB_URI`: MongoDB Atlas > Database > Connect
   - Vector Store IDs: OpenAI Dashboard > Storage > Vector Stores

### 3. Start the Server

**Using the startup script:**
```bash
# Windows
start.bat

# macOS/Linux
./start.sh
```

**Or manually:**
```bash
# Make sure virtual environment is activated
python main.py
```

The server will start at: **http://localhost:8000**

### 4. Test the API

#### Using the browser:

Visit the interactive API documentation:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

#### Using curl:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"content\": \"Hello, AI!\", \"threadId\": \"thread_abc123\"}"
```

#### Using Python requests:

```python
import requests

response = requests.post(
    "http://localhost:8000/chat",
    json={
        "content": "What are the main soil types?",
        "threadId": "thread_abc123"
    }
)

print(response.json())
```

### 5. Update Your Frontend

Update your Next.js frontend to point to the Python backend:

```typescript
// Before (Next.js API route)
const response = await fetch('/api/assistants/threads/[threadId]/messages', {
  method: 'POST',
  body: JSON.stringify({ content: message })
});

// After (Python FastAPI)
const response = await fetch('http://localhost:8000/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ 
    content: message,
    threadId: threadId 
  })
});

const data = await response.json();
console.log('Run ID:', data.run_id);
```

## Troubleshooting

### Port already in use

If port 8000 is already in use, you can change it:

```bash
# Edit main.py, change the port in the last section:
uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
```

Or run with uvicorn directly:
```bash
uvicorn main:app --port 8001 --reload
```

### MongoDB connection errors

1. Check your `MONGODB_URI` is correct
2. Verify your IP is whitelisted in MongoDB Atlas (Network Access)
3. Ensure your MongoDB user has read/write permissions

### OpenAI API errors

1. Verify your `OPENAI_API_KEY` is valid
2. Check you have credits in your OpenAI account
3. Ensure your `OPENAI_ASSISTANT_ID` exists

### Import errors

Make sure your virtual environment is activated:
```bash
# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate
```

Then reinstall dependencies:
```bash
pip install -r requirements.txt
```

## Development Tips

### Auto-reload on code changes

The server runs with `reload=True` by default, so any changes to `.py` files will automatically restart the server.

### View logs

All requests and errors are logged to the console. Watch for:
- `📨` New message received
- `✅` Successful operations
- `⚠️` Warnings (non-fatal)
- `❌` Errors

### Test with different models

Edit `main.py` and change the model in the `run_config`:

```python
run_config = {
    "model": "gpt-4o",  # Change to gpt-4o for higher quality (more expensive)
    # ... other config
}
```

### Adjust token limits

Modify `max_completion_tokens` in `main.py`:

```python
"max_completion_tokens": 2000,  # Increase for longer responses
```

## Next Steps

- [ ] Test the `/chat` endpoint with your frontend
- [ ] Set up proper error handling in your frontend
- [ ] Consider adding authentication/authorization
- [ ] Set up monitoring and logging for production
- [ ] Deploy to a cloud provider (AWS, Azure, Google Cloud, etc.)

## Support

If you encounter issues:

1. Check the console logs for detailed error messages
2. Verify all environment variables are set correctly
3. Test the OpenAI and MongoDB connections independently
4. Check the interactive API docs at http://localhost:8000/docs

## Differences from Next.js Version

| Feature | Next.js | Python FastAPI |
|---------|---------|----------------|
| Response Type | Streaming | Fire-and-forget (returns run_id) |
| Runtime | Node.js | Python |
| Type System | TypeScript interfaces | Pydantic models |
| Database | MongoDB (Node driver) | Motor (async driver) |
| API Docs | Manual | Auto-generated (Swagger/ReDoc) |
| CORS | Next.js built-in | Explicit middleware |

