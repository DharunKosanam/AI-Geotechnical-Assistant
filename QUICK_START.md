# 🚀 Quick Start - Python Backend Integration

## What Changed?

Your frontend now talks to the **Python FastAPI backend** instead of Next.js API routes for sending messages.

---

## ⚡ Start Both Servers

### 1. Start Python Backend (Terminal 1)

```bash
cd python_backend
start.bat          # Windows
./start.sh         # macOS/Linux
```

**Runs on:** `http://127.0.0.1:8000`

### 2. Start Next.js Frontend (Terminal 2)

```bash
npm run dev
```

**Runs on:** `http://localhost:3000`

---

## ✅ Test It

1. Open browser to `http://localhost:3000`
2. Send a message
3. Watch it stream in real-time from Python backend! 🎉

---

## 🔄 Switch Backends

**File:** `app/config/api.ts`

```typescript
// Use Python backend (current):
export const BACKEND_TYPE: 'python' | 'nextjs' = 'python';

// Use Next.js backend (original):
export const BACKEND_TYPE: 'python' | 'nextjs' = 'nextjs';
```

After changing, restart `npm run dev`.

---

## 🔍 Verify It's Working

### Python Backend Logs:

```
📨 Received message for thread: thread_abc123
✅ Message added to thread successfully
✅ Created new conversation in MongoDB: thread_abc123
Starting streaming run for thread: thread_abc123
```

### Browser Console (F12):

Should see request to: `http://127.0.0.1:8000/chat/stream`

---

## 🆘 Troubleshooting

### CORS Error?

Make sure both servers are running:
- Python: `http://127.0.0.1:8000`
- Frontend: `http://localhost:3000`

### Connection Refused?

Python backend not running. Start it with:
```bash
cd python_backend
python main.py
```

### 401 Error?

Check `python_backend/.env` has your `OPENAI_API_KEY`.

---

## 📚 More Info

- **Detailed Guide**: `FRONTEND_MIGRATION_GUIDE.md`
- **Python Backend Docs**: `python_backend/README.md`
- **Python Setup**: `python_backend/SETUP.md`
- **Migration Notes**: `PYTHON_MIGRATION.md`
- **API Docs**: http://127.0.0.1:8000/docs

---

**That's it!** Your app is now using the Python backend. 🐍✨

