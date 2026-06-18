"""
Pytest configuration file with shared fixtures for testing.
"""
import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, patch
from app.main import app


@pytest_asyncio.fixture
async def async_client():
    """
    Provides an async HTTP client for testing FastAPI endpoints.
    
    Uses httpx.AsyncClient with ASGITransport to mount the FastAPI app.
    This allows us to test the API without actually starting a server.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def sample_pdf_bytes():
    """
    Returns minimal valid PDF file bytes for testing file uploads.
    
    This is the smallest possible valid PDF file.
    """
    # Minimal PDF file (1 page, empty)
    pdf_content = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj
2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj
3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
>>
endobj
4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
100 700 Td
(Test PDF) Tj
ET
endstream
endobj
xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000214 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
307
%%EOF"""
    return pdf_content


@pytest.fixture(autouse=True)
def mock_groq_llm(monkeypatch):
    """
    Mock Groq LLM to avoid external API dependency.
    
    This fixture automatically applies to all tests (autouse=True).
    Returns a realistic mock response for chat completions.
    """
    async def mock_generate_answer(query: str, context: str, history: list = None):
        """Mock LLM response based on query"""
        return f"This is a test response to your query: '{query}'. Based on the provided context about geotechnical engineering, I can provide detailed information. The context includes relevant technical documentation."
    
    # Patch at the router level where it's imported
    monkeypatch.setattr(
        "app.routers.chat.generate_answer_with_groq",
        mock_generate_answer
    )
    
    return mock_generate_answer


@pytest.fixture(autouse=True)
async def mock_mongodb_operations(monkeypatch):
    """
    Mock MongoDB operations to avoid event loop issues.
    
    This fixture automatically applies to all tests (autouse=True).
    Mocks the database cursor operations that cause event loop closure.
    """
    # Mock data for file listing
    from datetime import datetime
    mock_files_data = [
        {
            "_id": "test_id_1",
            "source": "test_document.pdf",
            "filename": "test_document.pdf",  # Add filename field
            "category": "test",
            "userId": "test_user",
            "uploadedAt": datetime.now(),
            "createdAt": datetime.now(),
            "bytes": 454,
            "purpose": "assistants",
            "metadata": {
                "filename": "test_document.pdf",
                "category": "test"
            }
        },
        {
            "_id": "test_id_2",
            "source": "sample.pdf",
            "filename": "sample.pdf",  # Add filename field
            "category": "test",
            "userId": "test_user",
            "uploadedAt": datetime.now(),
            "createdAt": datetime.now(),
            "bytes": 854,
            "purpose": "assistants",
            "metadata": {
                "filename": "sample.pdf",
                "category": "test"
            }
        }
    ]
    
    # Create a mock cursor that implements async iteration
    class MockCursor:
        def __init__(self, data):
            self.data = data
            self.index = 0
        
        def __aiter__(self):
            return self
        
        async def __anext__(self):
            if self.index >= len(self.data):
                raise StopAsyncIteration
            item = self.data[self.index]
            self.index += 1
            return item
        
        def sort(self, *args, **kwargs):
            return self
        
        def allow_disk_use(self, *args, **kwargs):
            return self
    
    # Mock the files_collection.find() method
    original_find = None
    try:
        from app.core.database import files_collection
        original_find = files_collection.find
        
        def mock_find(*args, **kwargs):
            # Filter based on category if provided
            filter_dict = args[0] if args else kwargs.get('filter', {})
            category = filter_dict.get('category')
            
            if category:
                filtered_data = [doc for doc in mock_files_data if doc.get('category') == category]
            else:
                filtered_data = mock_files_data
            
            return MockCursor(filtered_data)
        
        files_collection.find = mock_find
    except Exception as e:
        print(f"Warning: Could not mock MongoDB find: {e}")
    
    yield
    
    # Restore original if it was mocked
    if original_find:
        try:
            from app.core.database import files_collection
            files_collection.find = original_find
        except:
            pass
