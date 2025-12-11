# AI Geotechnical Chat - Python FastAPI Backend

This is a Python FastAPI backend migrated from the Next.js TypeScript implementation. It provides a REST API for the AI Geotechnical Chat application.

## Features

- **FastAPI** - Modern, fast web framework for building APIs
- **OpenAI Assistants API** - Integration with GPT-4o-mini
- **MongoDB** - Async database operations using Motor
- **CORS** - Configured for frontend at `localhost:3000`
- **Fire-and-Forget Pattern** - Returns run_id immediately without waiting for completion

## Setup

### 1. Install Dependencies

```bash
cd python_backend
pip install -r requirements.txt
```

Or use a virtual environment (recommended):

```bash
cd python_backend
python -m venv venv

# On Windows
venv\Scripts\activate

# On macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in your actual values:

```bash
cp .env.example .env
```

Required variables:
- `OPENAI_API_KEY` - Your OpenAI API key
- `OPENAI_ASSISTANT_ID` - Your OpenAI Assistant ID
- `MONGODB_URI` - Your MongoDB connection string

Optional variables:
- `OPENAI_VECTOR_STORE_ID` - User-uploaded files vector store
- `OPENAI_KNOWLEDGE_STORE_ID` - Knowledge base vector store

### 3. Run the Server

```bash
python main.py
```

Or using uvicorn directly:

```bash
uvicorn main:app --reload --port 8000
```

The API will be available at: `http://localhost:8000`

## API Endpoints

### `POST /chat`

Send a message to the AI assistant.

**Request Body:**
```json
{
  "content": "What are the soil classification types?",
  "threadId": "thread_abc123",
  "assistantId": "asst_xyz789"  // Optional
}
```

**Response:**
```json
{
  "success": true,
  "run_id": "run_abc123",
  "thread_id": "thread_abc123",
  "message": "Message sent successfully"
}
```

### `GET /`

Health check endpoint.

**Response:**
```json
{
  "status": "ok",
  "message": "AI Geotechnical Chat API is running",
  "version": "1.0.0"
}
```

## API Documentation

FastAPI provides automatic interactive API documentation:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## Architecture

### Request Flow

1. **Receive Message** - Accept user message via POST /chat
2. **Cancel Active Runs** - Check for and cancel any active runs to prevent race conditions
3. **Add to Thread** - Add user message to OpenAI thread
4. **Save to MongoDB** - Store/update conversation in MongoDB
5. **Create Run** - Start assistant run with GPT-4o-mini
6. **Return Immediately** - Return run_id without waiting (fire-and-forget)

### MongoDB Schema

Collection: `conversations`

```javascript
{
  userId: "default-user",
  threadId: "thread_abc123",
  name: "2024-12-11 10:30:00",
  isGroup: false,
  createdAt: ISODate("2024-12-11T10:30:00Z"),
  updatedAt: ISODate("2024-12-11T10:30:00Z")
}
```

## Migration Notes

This backend mirrors the Next.js implementation with the following changes:

1. **Streaming → Fire-and-Forget**: The Next.js version streams responses. This Python version returns the `run_id` immediately.
2. **Async/Await**: Uses Python's async/await for non-blocking operations.
3. **Pydantic Models**: Request/response validation using Pydantic instead of TypeScript interfaces.
4. **Motor**: Async MongoDB driver instead of the standard MongoDB Node.js driver.

## Development

### Project Structure

```
python_backend/
├── main.py              # FastAPI application and endpoints
├── models.py            # Pydantic models (contracts)
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variables template
└── README.md           # This file
```

### Error Handling

The API handles various error scenarios:

- **401 Unauthorized** - Invalid OpenAI API key
- **400 Bad Request** - Missing/invalid parameters or active run conflict
- **404 Not Found** - Thread not found
- **500 Internal Server Error** - Unexpected errors

All errors return a JSON response with `error` and optional `details` fields.

## Testing

Test the API using curl:

```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Hello, what are geotechnical considerations for foundations?",
    "threadId": "thread_abc123"
  }'
```

Or use the interactive Swagger UI at http://localhost:8000/docs

## Production Deployment

For production, consider:

1. **Environment Variables** - Use secrets management (AWS Secrets Manager, Azure Key Vault, etc.)
2. **Process Manager** - Use gunicorn or supervisor to manage the process
3. **Reverse Proxy** - Use nginx or similar for SSL/TLS termination
4. **Monitoring** - Add logging and monitoring (Sentry, DataDog, etc.)
5. **Rate Limiting** - Add rate limiting middleware
6. **Authentication** - Implement proper user authentication

Example production command:

```bash
gunicorn main:app \
  --workers 4 \
  --worker-class uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --timeout 120
```

## License

Same as the parent project.

