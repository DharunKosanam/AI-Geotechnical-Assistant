# Python Backend Migration Guide

This document explains the migration from Next.js API routes to Python FastAPI.

## 🎯 Overview

Your Next.js backend API has been successfully migrated to Python FastAPI. All files are contained in the `python_backend/` folder, and **no existing Next.js files have been modified**.

## 📁 New Files Created

```
python_backend/
├── main.py              # FastAPI server with /chat endpoint
├── models.py            # Pydantic models (TypeScript interfaces → Python)
├── requirements.txt     # Python dependencies
├── env.example          # Environment variables template
├── .gitignore          # Python-specific gitignore
├── README.md           # Comprehensive documentation
├── SETUP.md            # Quick setup guide
├── start.bat           # Windows startup script
└── start.sh            # Unix/Linux/macOS startup script
```

## 🔄 Migration Mapping

### TypeScript → Python Equivalents

| Next.js (TypeScript) | Python (FastAPI) |
|---------------------|------------------|
| `app/api/assistants/threads/[threadId]/messages/route.ts` | `python_backend/main.py` (`/chat` endpoint) |
| TypeScript interfaces | `python_backend/models.py` (Pydantic models) |
| `openai` package | `openai` package (AsyncOpenAI) |
| MongoDB Node.js driver | `motor` (async MongoDB driver) |
| `lib/mongodb.js` | Inline in `main.py` (AsyncIOMotorClient) |

### Key Differences

#### 1. **Response Type**
- **Next.js**: Returns a streaming response with SSE (Server-Sent Events)
- **Python**: Returns `run_id` immediately (fire-and-forget pattern)

#### 2. **Request Format**
```typescript
// Next.js - Dynamic route parameter
POST /api/assistants/threads/[threadId]/messages
Body: { content: "message" }

// Python - Single endpoint with threadId in body
POST /chat
Body: { content: "message", threadId: "thread_xxx" }
```

#### 3. **Configuration**
```typescript
// Next.js - Multiple config files
app/openai.ts
app/assistant-config.ts
lib/mongodb.js

// Python - Single file with inline config
main.py (all configuration in one place)
```

## 🚀 Quick Start

### 1. Setup Python Backend

```bash
cd python_backend

# Copy environment template
copy env.example .env  # Windows
cp env.example .env    # macOS/Linux

# Edit .env with your actual values
# Then start the server:
start.bat              # Windows
./start.sh             # macOS/Linux
```

The Python backend will run on: **http://localhost:8000**

### 2. Update Frontend (Optional)

If you want to switch your frontend to use the Python backend:

```typescript
// Before - Next.js API route
const response = await fetch(`/api/assistants/threads/${threadId}/messages`, {
  method: 'POST',
  body: JSON.stringify({ content: userMessage })
});

// Stream response handling...
const reader = response.body?.getReader();
// ... stream processing

// After - Python FastAPI (fire-and-forget)
const response = await fetch('http://localhost:8000/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ 
    content: userMessage,
    threadId: threadId 
  })
});

const data = await response.json();
// Returns: { success: true, run_id: "run_xxx", thread_id: "thread_xxx" }

// Then poll for completion or use webhooks
```

## 🔑 Environment Variables

Copy these from your existing `.env` or `.env.local`:

```env
# Required
OPENAI_API_KEY=sk-proj-xxxxx
OPENAI_ASSISTANT_ID=asst_xxxxx
MONGODB_URI=mongodb+srv://user:pass@cluster.mongodb.net/...

# Optional
OPENAI_VECTOR_STORE_ID=vs_xxxxx
OPENAI_KNOWLEDGE_STORE_ID=vs_xxxxx
```

## 📊 Feature Comparison

| Feature | Next.js Implementation | Python Implementation |
|---------|----------------------|---------------------|
| **Framework** | Next.js 14+ | FastAPI |
| **Language** | TypeScript | Python 3.8+ |
| **OpenAI SDK** | openai (Node.js) | openai (Python async) |
| **Database** | MongoDB (sync) | Motor (async) |
| **Response** | Streaming SSE | JSON (run_id) |
| **Active Run Check** | ✅ Yes | ✅ Yes |
| **Message Creation** | ✅ Yes | ✅ Yes |
| **MongoDB Save** | ❌ No (via separate route) | ✅ Yes (inline) |
| **Vector Store** | ✅ Yes | ✅ Yes |
| **Error Handling** | ✅ Comprehensive | ✅ Comprehensive |
| **CORS** | ✅ Next.js default | ✅ Explicit middleware |
| **API Docs** | ❌ Manual | ✅ Auto-generated (Swagger) |

## 🧪 Testing Both Versions

You can run both backends simultaneously for testing:

1. **Next.js backend**: http://localhost:3000 (your existing setup)
2. **Python backend**: http://localhost:8000 (new Python API)

### Test the Python Backend

```bash
# Health check
curl http://localhost:8000/

# Send a message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"content": "Hello!", "threadId": "thread_abc123"}'
```

Or visit the interactive API docs:
- http://localhost:8000/docs (Swagger UI)
- http://localhost:8000/redoc (ReDoc)

## 🎨 Implementation Details

### MongoDB Schema Match

The Python backend uses the **exact same schema** as your Next.js app:

```javascript
// conversations collection
{
  userId: "default-user",
  threadId: "thread_xxx",
  name: "2024-12-11 10:30:00",
  isGroup: false,
  createdAt: ISODate("..."),
  updatedAt: ISODate("...")
}
```

### OpenAI Configuration Match

Both implementations use the same OpenAI configuration:

- ✅ Truncation strategy (last 10 messages)
- ✅ Max completion tokens (1000)
- ✅ Model: gpt-4o-mini
- ✅ Vector store attachment
- ✅ Tool resources configuration

### Error Handling Match

Both handle the same error scenarios:

- ✅ 401 - Invalid API key
- ✅ 400 - Invalid request / active run conflict
- ✅ 404 - Thread not found
- ✅ 500 - Internal server error

## 🔒 Security Considerations

Both implementations:
- ✅ Load environment variables from `.env`
- ✅ Never expose API keys in responses
- ✅ Validate all inputs
- ✅ Handle errors gracefully

**For production**, consider adding:
- Authentication/authorization
- Rate limiting
- Request validation
- Logging and monitoring
- HTTPS/TLS

## 📈 Performance

### Python Backend Advantages
- ✅ Async/await for non-blocking I/O
- ✅ Efficient connection pooling (MongoDB Motor)
- ✅ Fast JSON serialization (Pydantic)
- ✅ Lower memory footprint

### Trade-offs
- ⚠️  No streaming (returns run_id instead)
- ⚠️  Client needs to poll for completion or use webhooks

## 🛠️ Development Workflow

### Continue using Next.js (current)
```bash
npm run dev
# Access at http://localhost:3000
```

### Test Python backend (new)
```bash
cd python_backend
start.bat  # or ./start.sh
# Access at http://localhost:8000
```

### Gradual Migration Strategy

1. **Phase 1**: Run both backends side-by-side
2. **Phase 2**: Test Python backend with a subset of users
3. **Phase 3**: Gradually shift traffic to Python backend
4. **Phase 4**: Deprecate Next.js API routes (keep frontend)

## 📝 Next Steps

- [ ] Set up Python environment and install dependencies
- [ ] Configure `.env` file with your credentials
- [ ] Start the Python backend and test the `/chat` endpoint
- [ ] Test with your frontend (optional)
- [ ] Compare performance and reliability
- [ ] Deploy to production (if satisfied)

## 🆘 Support

If you encounter issues:

1. **Check logs**: Both backends log extensively
2. **Compare behavior**: Test the same request on both backends
3. **Verify environment**: Ensure all env vars are set correctly
4. **Test independently**: Use curl or the Swagger UI to isolate issues

## 📚 Documentation

- **Python Backend**: `python_backend/README.md`
- **Quick Setup**: `python_backend/SETUP.md`
- **API Reference**: http://localhost:8000/docs (when running)

---

**Note**: Your original Next.js files remain **completely unchanged**. The Python backend is a parallel implementation in a separate folder.

