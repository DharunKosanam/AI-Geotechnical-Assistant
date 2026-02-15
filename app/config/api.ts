/**
 * API Configuration
 * 
 * This file manages API endpoints for the application.
 * You can easily switch between Python and Next.js backends here.
 */

// Backend selection
// Set to 'python' to use Python FastAPI backend
// Set to 'nextjs' to use Next.js API routes
type BackendType = 'python' | 'nextjs';
export const BACKEND_TYPE: BackendType = 'python' as BackendType;

// Python backend URL
const PYTHON_BACKEND_URL = process.env.NEXT_PUBLIC_PYTHON_API_URL || 'http://127.0.0.1:8000';

// API endpoints based on backend type
export const API_ENDPOINTS = {
  // Message sending endpoint
  sendMessage: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/chat`;
    }
    return `/api/assistants/threads/${threadId}/messages`;
  },
  
  // Thread management
  createThread: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads`;
    }
    return `/api/assistants/threads`;
  },
  
  getThreadHistory: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/history`;
    }
    return `/api/assistants/threads/history`;
  },
  
  updateThread: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/history`;
    }
    return `/api/assistants/threads/history`;
  },
  
  deleteThread: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/history`;
    }
    return `/api/assistants/threads/history`;
  },
  
  createThreadHistory: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/history`;
    }
    return `/api/assistants/threads/history`;
  },
  
  // Thread title generation
  generateTitle: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/${threadId}/title`;
    }
    return `/api/assistants/threads/${threadId}/title`;
  },
  
  // Tool actions
  submitActions: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/${threadId}/actions`;
    }
    return `/api/assistants/threads/${threadId}/actions`;
  },
  
  // Messages history - this is the critical endpoint for loading thread messages
  getMessages: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/assistants/threads/${threadId}/messages-history`;
    }
    return `/api/assistants/threads/${threadId}/messages-history`;
  },
  
  // Alias for getMessages (some components use this)
  getThreadMessages: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/chat/${threadId}/history`;  // Use new endpoint
    }
    return `/api/assistants/threads/${threadId}/history`;
  },
  
  // Direct chat history endpoint
  getChatHistory: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/chat/${threadId}/history`;
    }
    return `/api/assistants/threads/${threadId}/history`;
  },
  
  // File management endpoints
  uploadFile: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/upload`;
    }
    return `/api/files/upload`;
  },
  
  listFiles: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/files`;
    }
    return `/api/files`;
  },
  
  deleteFile: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/files`;
    }
    return `/api/files`;
  },
};

/**
 * Get the request body format for sending a message
 * Python backend uses RAG with query format, Next.js uses content
 */
export const getMessageRequestBody = (content: string, threadId: string | null) => {
  if (BACKEND_TYPE === 'python') {
    // Python RAG backend uses query and optional history
    return {
      query: content,
      history: [],
      threadId: threadId,  // Include threadId for message persistence
    };
  }
  // Next.js backend doesn't need threadId in body (it's in the URL)
  return {
    content,
  };
};

/**
 * Helper to check if we're using Python backend
 */
export const isPythonBackend = (): boolean => BACKEND_TYPE === 'python';

/**
 * Helper to check if we're using Next.js backend
 */
// eslint-disable-next-line @typescript-eslint/no-unnecessary-condition
export const isNextJSBackend = (): boolean => BACKEND_TYPE === 'nextjs';

