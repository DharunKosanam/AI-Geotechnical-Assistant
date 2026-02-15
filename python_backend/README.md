# AI Geotechnical Chat API - Backend

FastAPI backend with Groq LLM and MongoDB Atlas Vector Search for RAG-based document Q&A.

## Quick Start

### 1. Install Dependencies

**Windows:**
```bash
install_dependencies.bat
```

**Linux/Mac:**
```bash
chmod +x install_dependencies.sh
./install_dependencies.sh
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required environment variables:
- `GROQ_API_KEY` - Your Groq API key
- `MONGODB_URI` - MongoDB Atlas connection string
- `REDIS_HOST` - Redis Cloud host
- `REDIS_PORT` - Redis Cloud port (default: 6379)
- `REDIS_PASSWORD` - Redis Cloud password

### 3. Start Server

**Windows:**
```bash
start.bat
```

**Linux/Mac:**
```bash
chmod +x start.sh
./start.sh
```

Or directly:
```bash
uvicorn main:app --reload
```

Server will run at: http://127.0.0.1:8000

## API Documentation

Once running, visit:
- **Swagger UI:** http://127.0.0.1:8000/docs
- **ReDoc:** http://127.0.0.1:8000/redoc

## Main Endpoints

### Chat
- `POST /chat` - RAG-based chat with document context

### File Management
- `POST /api/upload` - Upload & process PDF documents
- `GET /api/assistants/files` - List uploaded files
- `DELETE /api/assistants/files` - Delete files

### Threads
- `POST /api/assistants/threads` - Create conversation thread
- `GET /api/assistants/threads/history` - List threads
- `GET /api/assistants/threads/{id}/messages-history` - Get messages

## Technology Stack

- **Framework:** FastAPI
- **LLM:** Groq (qwen/qwen3-32b)
- **Vector Search:** MongoDB Atlas Vector Search
- **Embeddings:** FastEmbed (BAAI/bge-small-en-v1.5, 384-dim)
- **Database:** MongoDB with Motor
- **PDF Processing:** pypdf

## MongoDB Atlas Setup

Create a vector search index on the `files` collection:

```json
{
  "fields": [
    {
      "type": "vector",
      "path": "embedding",
      "numDimensions": 384,
      "similarity": "cosine"
    }
  ]
}
```

Index name: `vector_index`

## Project Structure

```
python_backend/
├── app/
│   ├── core/           # Configuration & database
│   ├── routers/        # API endpoints
│   └── services/       # Business logic
├── main.py             # Application entry point
├── models.py           # Pydantic models
└── requirements.txt    # Dependencies
```

## Development

Install in development mode:
```bash
pip install -r requirements.txt
```

Run with auto-reload:
```bash
uvicorn main:app --reload
```

## License

See LICENSE file in project root.

