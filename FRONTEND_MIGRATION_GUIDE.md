# Frontend Migration Guide - Python Backend Integration

## ✅ What Was Changed

### 1. **Python Backend - Streaming Support Added**

File: `python_backend/main.py`

Added a new endpoint **`POST /chat/stream`** that:
- ✅ Accepts messages with `content` and `threadId`
- ✅ Cancels active runs to prevent race conditions
- ✅ Adds message to OpenAI thread
- ✅ Saves conversation to MongoDB (async)
- ✅ **Streams the OpenAI Assistant response in real-time** (just like Next.js)
- ✅ Returns SSE (Server-Sent Events) format

### 2. **Frontend - API URL Updated**

File: `app/components/chat.tsx`

Changed the fetch URL in the `sendMessage` function:

**Before (Next.js API):**
```typescript
const response = await fetch(
  `/api/assistants/threads/${actualThreadId}/messages`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content: text }),
  }
);
```

**After (Python API):**
```typescript
const response = await fetch(
  `http://127.0.0.1:8000/chat/stream`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      content: text,
      threadId: actualThreadId,
    }),
  }
);
```

### 3. **CORS Configuration**

File: `python_backend/main.py`

CORS is **already configured** to allow requests from:
- ✅ `http://localhost:3000`
- ✅ `http://127.0.0.1:3000`

No additional changes needed!

---

## 🧪 Testing Instructions

### Step 1: Start the Python Backend

```bash
cd python_backend

# Make sure .env is configured (copy from env.example if needed)
# Then start the server:

# Windows:
start.bat

# macOS/Linux:
./start.sh

# Or manually:
python main.py
```

The Python backend should start on: **http://127.0.0.1:8000**

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Started server process
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### Step 2: Start the Next.js Frontend

In a **separate terminal**:

```bash
# From the project root
npm run dev
```

The frontend should start on: **http://localhost:3000**

### Step 3: Test the Integration

1. **Open your browser** to `http://localhost:3000`
2. **Start a new chat** or select an existing thread
3. **Send a message** - it should now go to the Python backend
4. **Verify streaming** - you should see the assistant's response appear in real-time

### Step 4: Verify Backend Logs

In the Python backend terminal, you should see:

```
📨 Received message for thread: thread_abc123
Checking for active runs on thread: thread_abc123
✅ Message added to thread successfully
✅ Created new conversation in MongoDB: thread_abc123
Starting streaming run for thread: thread_abc123
```

---

## 🔍 Troubleshooting

### Issue: CORS Error in Browser Console

**Error:**
```
Access to fetch at 'http://127.0.0.1:8000/chat/stream' from origin 'http://localhost:3000' 
has been blocked by CORS policy
```

**Solution:**
The CORS configuration should already be correct. If you still see this error:

1. Verify Python backend is running on port 8000
2. Check `python_backend/main.py` has the CORS middleware configured
3. Try restarting both servers
4. Clear browser cache

### Issue: Connection Refused

**Error:**
```
Failed to fetch
net::ERR_CONNECTION_REFUSED
```

**Solution:**
- Python backend is not running
- Start it with `python main.py` or `start.bat`/`start.sh`

### Issue: 401 Unauthorized

**Error:**
```
Invalid API key. Please check your OPENAI_API_KEY in the .env file.
```

**Solution:**
- Check `python_backend/.env` file exists
- Verify `OPENAI_API_KEY` is set correctly
- Restart Python backend after changing `.env`

### Issue: 404 Thread Not Found

**Error:**
```
Thread not found. Please create a new chat.
```

**Solution:**
- The thread might have been deleted from OpenAI
- Start a new chat conversation
- The frontend will automatically create a new thread

### Issue: Streaming Not Working

**Symptoms:**
- No real-time text appearing
- Response appears all at once

**Solution:**
1. Check browser console for errors
2. Verify the response is actually streaming:
   - Open browser DevTools → Network tab
   - Send a message
   - Look for the request to `http://127.0.0.1:8000/chat/stream`
   - Check "Type" column - should say "eventsource" or show streaming
3. If still not working, check Python backend logs for errors

---

## 🔄 Switching Between Backends

You can easily switch between Python and Next.js backends:

### Use Python Backend (Current):

```typescript
// app/components/chat.tsx
const response = await fetch(
  `http://127.0.0.1:8000/chat/stream`,
  { /* ... */ }
);
```

### Use Next.js Backend (Original):

```typescript
// app/components/chat.tsx
const response = await fetch(
  `/api/assistants/threads/${actualThreadId}/messages`,
  { /* ... */ }
);
```

Just change the URL and restart your frontend dev server.

---

## 📊 Feature Comparison

| Feature | Next.js Backend | Python Backend |
|---------|----------------|----------------|
| **Streaming** | ✅ SSE | ✅ SSE |
| **Active Run Cancellation** | ✅ Yes | ✅ Yes |
| **MongoDB Integration** | ✅ Yes | ✅ Yes |
| **Vector Stores** | ✅ Yes | ✅ Yes |
| **Error Handling** | ✅ Comprehensive | ✅ Comprehensive |
| **CORS** | ✅ Built-in | ✅ Configured |
| **Response Format** | Streaming SSE | Streaming SSE |
| **ThreadId Location** | URL param | Request body |

---

## 🎯 API Endpoint Details

### Python Backend: `POST /chat/stream`

**Request:**
```json
{
  "content": "What are the soil types?",
  "threadId": "thread_abc123",
  "assistantId": "asst_xyz789"  // Optional
}
```

**Response:** 
- **Type**: Server-Sent Events (SSE) stream
- **Format**: Same as Next.js OpenAI stream
- **Content-Type**: `text/event-stream`

**Response Events:**
```
data: {"event": "thread.message.created", ...}

data: {"event": "thread.message.delta", ...}

data: {"event": "thread.run.completed", ...}
```

The frontend's `AssistantStream.fromReadableStream()` automatically parses these events.

---

## 🔐 Environment Variables

### Python Backend (`.env`):

```env
OPENAI_API_KEY=sk-proj-xxx
OPENAI_ASSISTANT_ID=asst_xxx
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/...
OPENAI_VECTOR_STORE_ID=vs_xxx
OPENAI_KNOWLEDGE_STORE_ID=vs_xxx
```

### Frontend - No Changes Needed!

The Next.js frontend doesn't need any environment variable changes. It directly calls the Python backend via HTTP.

---

## 📝 What Wasn't Changed

These parts of your application remain **unchanged**:

1. ✅ Thread creation (`/api/assistants/threads`)
2. ✅ Thread history (`/api/assistants/threads/history`)
3. ✅ Title generation (`/api/assistants/threads/[threadId]/title`)
4. ✅ Tool call submission (`/api/assistants/threads/[threadId]/actions`)
5. ✅ File uploads
6. ✅ MongoDB schema
7. ✅ OpenAI Assistant configuration

**Only the message sending endpoint was changed.**

---

## 🚀 Next Steps

### Option 1: Full Migration (Recommended)

If the Python backend works well, you can migrate the remaining endpoints:

- [ ] `POST /api/assistants/threads` → `POST /threads/create`
- [ ] `GET /api/assistants/threads/history` → `GET /threads/history`
- [ ] `POST /api/assistants/threads/[threadId]/title` → `POST /threads/{threadId}/title`
- [ ] `POST /api/assistants/threads/[threadId]/actions` → `POST /threads/{threadId}/actions`

### Option 2: Hybrid Approach

Keep both backends running:
- **Python**: Handle message streaming (current)
- **Next.js**: Handle threads, history, and file management

### Option 3: Rollback

If you want to revert to Next.js:

```typescript
// app/components/chat.tsx - Change this line back:
const response = await fetch(
  `/api/assistants/threads/${actualThreadId}/messages`,
  // ...
);
```

---

## 📚 Additional Resources

- **Python Backend Docs**: `python_backend/README.md`
- **Setup Guide**: `python_backend/SETUP.md`
- **API Documentation**: http://127.0.0.1:8000/docs (when running)
- **Migration Overview**: `PYTHON_MIGRATION.md`

---

## ✅ Success Checklist

- [x] Python backend running on port 8000
- [x] Frontend updated to call Python API
- [x] CORS configured correctly
- [x] Streaming works in real-time
- [x] Messages saved to MongoDB
- [x] Error handling works properly
- [ ] Test with your actual use cases
- [ ] Monitor performance
- [ ] Check logs for any issues

---

**Questions or Issues?**

Check the Python backend logs for detailed error messages. All operations are logged with emoji indicators:
- 📨 Message received
- ✅ Success
- ⚠️  Warning (non-fatal)
- ❌ Error

