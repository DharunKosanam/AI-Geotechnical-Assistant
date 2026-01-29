"""
OpenAI service for managing AI interactions
"""
from openai import AsyncOpenAI
from app.core.config import OPENAI_API_KEY

# Initialize OpenAI client
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


def get_openai_client() -> AsyncOpenAI:
    """
    Get the OpenAI client instance
    
    Returns:
        AsyncOpenAI client
    """
    return openai_client

