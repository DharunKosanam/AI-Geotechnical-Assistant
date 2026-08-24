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

// Python backend base URL — intentionally EMPTY so client code calls FastAPI
// through SAME-ORIGIN relative paths (e.g. /auth/login). The browser must never
// see an absolute backend URL: it cannot reach localhost:8000, and a public
// absolute URL trips the browser's loopback/private-network + CORS rules.
// next.config.mjs rewrites() forwards these relative paths to the real FastAPI
// host SERVER-SIDE via the non-public PYTHON_API_URL env var.
export const PYTHON_BACKEND_URL = '';

// API endpoints based on backend type
export const API_ENDPOINTS = {
  // Message sending endpoint
  sendMessage: (threadId: string) => {
    if (BACKEND_TYPE === 'python') {
      // Slow RAG POST goes through a dedicated Route Handler (long timeout),
      // NOT the rewrites() proxy which resets on long requests.
      return `/api/chat`;
    }
    return `/api/assistants/threads/${threadId}/messages`;
  },

  // SSE variant of sendMessage. The client tries this first and falls back to
  // sendMessage() when it 404s, which is exactly what FastAPI returns while
  // STREAMING_ENABLED is off — so the backend flag alone controls the mode.
  sendMessageStream: () => `/api/chat/stream`,
  
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

  // Upload capability handshake -- which file types the picker should offer.
  // Backend-computed so the accept list follows server capability (e.g. images
  // appear only when vision extraction is enabled there). Fetched once on
  // mount; the client falls back to its static text-only defaults on failure.
  uploadConfig: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/upload/config`;
    }
    return `/api/files/upload/config`;
  },

  // File processing status — polled after upload while the backend ingests
  // (extraction/OCR/chunking/embedding) in a background task. threadId scopes
  // the lookup to the conversation the file was attached to, so the same
  // filename in another thread can't answer for this one.
  uploadStatus: (filename: string, threadId?: string) => {
    const params = new URLSearchParams({ filename });
    if (threadId) params.set("threadId", threadId);
    const qs = params.toString();
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/upload/status?${qs}`;
    }
    return `/api/files/upload/status?${qs}`;
  },
  
  listFiles: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/files`;
    }
    return `/api/files`;
  },

  // Raw stored bytes of an uploaded file (diagram PNG thumbnails). Reaches
  // FastAPI's GET /api/files/{id}/content via the beforeFiles rewrite in
  // next.config.mjs, which shadows the legacy OpenAI route handler.
  fileContent: (fileId: string) =>
    `${PYTHON_BACKEND_URL}/api/files/${encodeURIComponent(fileId)}/content`,
  
  deleteFile: () => {
    if (BACKEND_TYPE === 'python') {
      return `${PYTHON_BACKEND_URL}/api/files`;
    }
    return `/api/files`;
  },

  // --- Source-grounded output formats (SOURCE_FORMATS_ENABLED). Status is the
  // feature handshake (fails closed); stream is the SSE generation endpoint,
  // both via dedicated Route Handlers with streaming-safe timeouts. ---
  formatsStatus: (threadId?: string) =>
    threadId
      ? `/api/formats/status?threadId=${encodeURIComponent(threadId)}`
      : `/api/formats/status`,
  formatsStream: () => `/api/formats/stream`,

  // --- Named persistent source sets (SOURCE_SETS_ENABLED). Same-origin;
  // proxied to FastAPI by the /api/assistants rewrite. Status is the feature
  // handshake; sources lists a thread's documents; remove is dry-run by
  // default and deletes only with confirm: true. ---
  sourceSetsStatus: () => `/api/assistants/threads/sources-status`,
  threadSources: (threadId: string) =>
    `/api/assistants/threads/${encodeURIComponent(threadId)}/sources`,
  removeThreadSource: (threadId: string) =>
    `/api/assistants/threads/${encodeURIComponent(threadId)}/sources/remove`,

  // --- Student knowledge-base upload (Phase 4/5). Same-origin; proxied to
  // FastAPI by next.config rewrites(). ---
  kbStatus: () => `${PYTHON_BACKEND_URL}/api/kb/status`,
  kbUpload: () => `${PYTHON_BACKEND_URL}/api/kb/upload`,
  kbBulkUpload: () => `${PYTHON_BACKEND_URL}/api/kb/bulk-upload`,
  kbBatchStatus: (batchId: string) => `${PYTHON_BACKEND_URL}/api/kb/batch/${batchId}`,
  kbMyUploads: () => `${PYTHON_BACKEND_URL}/api/kb/my-uploads`,
  // Web-page ingestion by pasted URL (WEB_INGEST_ENABLED). Routes exist only
  // when the backend flag is on; the UI gates on kbStatus().webIngest.
  kbWebPreview: () => `${PYTHON_BACKEND_URL}/api/kb/web/preview`,
  kbWebIngest: () => `${PYTHON_BACKEND_URL}/api/kb/web/ingest`,

  // --- Message highlights + notes (HIGHLIGHTS_ENABLED). Same-origin; proxied
  // to FastAPI by the /api/assistants rewrite. The routes exist only when the
  // backend flag is on; the frontend learns that from /api/upload/config. ---
  threadHighlights: (threadId: string) =>
    `${PYTHON_BACKEND_URL}/api/assistants/threads/${encodeURIComponent(threadId)}/highlights`,
  threadHighlight: (threadId: string, highlightId: string) =>
    `${PYTHON_BACKEND_URL}/api/assistants/threads/${encodeURIComponent(threadId)}/highlights/${encodeURIComponent(highlightId)}`,
  // Thread-wide export (Markdown or Excel) as a file download.
  threadHighlightsExport: (threadId: string, format: "md" | "xlsx") =>
    `${PYTHON_BACKEND_URL}/api/assistants/threads/${encodeURIComponent(threadId)}/highlights/export?format=${format}`,
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

