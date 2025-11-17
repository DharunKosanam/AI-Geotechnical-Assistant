# Complete Project Explanation

## 📋 Table of Contents
1. [Project Overview](#project-overview)
2. [Technology Stack](#technology-stack)
3. [Project Structure](#project-structure)
4. [File-by-File Explanation](#file-by-file-explanation)
5. [How It Works](#how-it-works)
6. [How to Run](#how-to-run)
7. [Configuration](#configuration)
8. [Key Features](#key-features)

---

## 🎯 Project Overview

This is a **Geotechnical Assistant** web application built with Next.js and OpenAI's Assistant API. It's a specialized knowledge management system that allows teams to:

- Chat with an AI assistant that can access uploaded files
- Upload and manage files in a vector store for knowledge retrieval
- Maintain conversation history across multiple threads
- Share conversations with team members (group chats)
- Use code interpreter and file search capabilities

The application is designed for collaborative research and knowledge sharing, particularly for geotechnical engineering teams.

---

## 🛠 Technology Stack

### Core Technologies
- **Next.js 14.2.18** - React framework with App Router
- **React 18** - UI library
- **TypeScript 5.4.5** - Type safety
- **OpenAI SDK 4.46.0** - Integration with OpenAI Assistant API

### Key Libraries
- **react-markdown 9.0.1** - Render markdown in chat messages
- **csv-parser 3.0.0** - Parse CSV files
- **xlsx 0.18.5** - Handle Excel files

### Development Tools
- **Node.js** - Runtime environment
- **npm** - Package manager

---

## 📁 Project Structure

```
Team-Specialized-Knowledge/
│
├── app/                          # Next.js App Router directory
│   ├── api/                      # API routes (backend)
│   │   ├── assistants/
│   │   │   ├── route.ts         # Create assistant
│   │   │   ├── files/
│   │   │   │   └── route.tsx    # File upload/list/delete
│   │   │   └── threads/
│   │   │       ├── route.ts     # Create thread
│   │   │       ├── history/
│   │   │       │   └── route.ts # Thread history management
│   │   │       └── [threadId]/
│   │   │           ├── messages/
│   │   │           │   └── route.ts # Send messages
│   │   │           ├── actions/
│   │   │           │   └── route.ts # Handle tool calls
│   │   │           └── history/
│   │   │               └── route.ts # Get thread messages
│   │   └── files/
│   │       └── [fileId]/
│   │           └── route.ts     # Download files
│   │
│   ├── components/               # React components
│   │   ├── chat.tsx             # Main chat interface
│   │   ├── file-viewer.tsx      # File management UI
│   │   ├── thread-list.tsx      # Conversation history sidebar
│   │   └── warnings.tsx         # API key warning
│   │
│   ├── assistant-config.ts      # Assistant ID configuration
│   ├── openai.ts                # OpenAI client initialization
│   ├── layout.tsx               # Root layout
│   ├── page.tsx                 # Main page
│   └── globals.css              # Global styles
│
├── public/                       # Static assets
│   └── openai.svg               # Logo
│
├── userThreads.json             # Local thread storage
├── package.json                 # Dependencies
├── tsconfig.json                # TypeScript config
├── next.config.mjs              # Next.js config
├── jsconfig.json                # JavaScript path aliases
└── README.md                    # User guide
```

---

## 📄 File-by-File Explanation

### Configuration Files

#### `package.json`
- Defines project dependencies and scripts
- **Scripts:**
  - `npm run dev` - Start development server
  - `npm run build` - Build for production
  - `npm run start` - Start production server
  - `npm run lint` - Run linter

#### `tsconfig.json`
- TypeScript compiler configuration
- Sets path aliases (`@/*` maps to root directory)
- Enables JSX and modern ES features

#### `next.config.mjs`
- Next.js configuration (currently minimal/default)

#### `jsconfig.json`
- JavaScript path aliases for VS Code IntelliSense

---

### Core Application Files

#### `app/openai.ts`
```typescript
import OpenAI from "openai";
export const openai = new OpenAI();
```
- Initializes OpenAI client
- Uses `OPENAI_API_KEY` from environment variables
- Exported for use across the application

#### `app/assistant-config.ts`
```typescript
export let assistantId = "asst_5D5B1w7U2iMYGPcNtBkADIW9";
```
- Stores the OpenAI Assistant ID
- Can be overridden with `OPENAI_ASSISTANT_ID` environment variable
- This is the ID of your pre-configured assistant

#### `app/layout.tsx`
- Root layout component
- Checks for API key and shows warning if missing
- Sets metadata (title, description, favicon)
- Wraps all pages

#### `app/page.tsx`
- Main page component
- Renders the chat interface and file viewer side-by-side
- Uses CSS modules for styling

---

### API Routes (Backend)

#### `app/api/assistants/route.ts`
- **POST**: Creates a new OpenAI assistant
- Configures assistant with:
  - Code interpreter tool
  - File search tool
  - Custom function: `get_weather`
- Returns the assistant ID

#### `app/api/assistants/files/route.tsx`
- **POST**: Uploads a file to the assistant's vector store
  - Receives file as FormData
  - Uploads to OpenAI
  - Adds to vector store (creates if doesn't exist)
  
- **GET**: Lists all files in the vector store
  - Returns file ID, filename, and status
  - Limits to 20 most recent files
  
- **DELETE**: Removes a file from the vector store
  - Takes fileId in request body

- **Helper Function**: `getOrCreateVectorStore()`
  - Retrieves existing vector store from assistant
  - Creates new one if it doesn't exist
  - Attaches vector store to assistant

#### `app/api/assistants/threads/route.ts`
- **POST**: Creates a new conversation thread
- Returns thread ID for the new conversation

#### `app/api/assistants/threads/history/route.ts`
- **GET**: Retrieves all saved threads from `userThreads.json`
- **POST**: Saves a new thread with name and optional group flag
- **PUT**: Updates thread name or group status
- **DELETE**: Removes thread from local storage (and OpenAI if not a group)

#### `app/api/assistants/threads/[threadId]/messages/route.ts`
- **POST**: Sends a message to a thread
  - Creates user message in thread
  - Streams assistant response
  - Returns readable stream for real-time updates

#### `app/api/assistants/threads/[threadId]/actions/route.ts`
- **POST**: Handles tool/function calls from assistant
  - Submits tool outputs back to OpenAI
  - Continues the conversation stream

#### `app/api/assistants/threads/[threadId]/history/route.ts`
- **GET**: Retrieves all messages from a specific thread
  - Returns thread ID and message array

#### `app/api/files/[fileId]/route.ts`
- **GET**: Downloads a file by ID
  - Retrieves file content from OpenAI
  - Returns file with proper headers for download

---

### React Components (Frontend)

#### `app/components/chat.tsx`
**Main Chat Interface Component**

**Key Features:**
- Message display (user, assistant, code)
- Real-time streaming responses
- Thread management
- Group chat support
- Auto-scrolling to latest message

**State Management:**
- `userInput` - Current input text
- `messages` - Array of message objects
- `threadId` - Current conversation thread ID
- `isGroupConversation` - Whether current thread is a group
- `inputDisabled` - Disables input during processing

**Key Functions:**
- `sendMessage()` - Sends user message to API
- `handleReadableStream()` - Processes streaming responses
- `loadThread()` - Loads conversation history
- `createNewThread()` - Starts a new conversation
- `handleThreadSelect()` - Switches between threads
- `handleJoinTeam()` - Joins a group chat

**Stream Event Handlers:**
- `textCreated` - New assistant message started
- `textDelta` - Text chunk received (streaming)
- `imageFileDone` - Image file ready
- `toolCallCreated` - Code interpreter started
- `toolCallDelta` - Code output received
- `requires_action` - Function call needed
- `run.completed` - Assistant finished responding

#### `app/components/file-viewer.tsx`
**File Management Component**

**Features:**
- Lists uploaded files (max 20)
- Upload new files
- Delete files
- Shows file status (processing, completed, etc.)

**Functions:**
- `fetchFiles()` - Loads file list from API
- `handleFileUpload()` - Uploads file via FormData
- `handleFileDelete()` - Removes file from vector store

#### `app/components/thread-list.tsx`
**Conversation History Sidebar**

**Features:**
- Lists all conversation threads
- Edit thread names
- Delete threads
- Share threads (group chat)
- Join team chats

**Functions:**
- `fetchThreads()` - Loads threads from API
- `deleteThread()` - Removes thread
- `updateThreadName()` - Renames thread
- `toggleGroupStatus()` - Makes thread shareable
- `handleCopyThreadId()` - Copies thread ID to clipboard

#### `app/components/warnings.tsx`
**API Key Warning Component**
- Shows instructions when API key is missing
- Displays setup steps for `.env` file

---

### Data Storage

#### MongoDB Database (`ai-geotech-db`)

**Collection: `conversations`**
```json
{
  "_id": "MongoDB ObjectId",
  "userId": "default-user",
  "threadId": "thread_xxx",
  "name": "Conversation Name",
  "isGroup": true/false,
  "createdAt": "2025-11-17T12:00:00.000Z",
  "updatedAt": "2025-11-17T12:00:00.000Z"
}
```
- Stores conversation thread metadata in MongoDB Atlas
- Persists thread names and group status
- Used for conversation history sidebar
- Connection managed via `lib/mongodb.js` with singleton pattern

---

## 🔄 How It Works

### 1. Initialization Flow
1. User opens application
2. `layout.tsx` checks for `OPENAI_API_KEY`
3. If missing, shows warning component
4. If present, loads main page

### 2. Chat Flow
1. User sends a message
2. `chat.tsx` calls `/api/assistants/threads/[threadId]/messages`
3. API creates user message in OpenAI thread
4. API streams assistant response
5. Frontend processes stream events in real-time
6. Messages appear as they're generated
7. Code interpreter output shown separately
8. File references are linked and downloadable

### 3. File Upload Flow
1. User selects file in `file-viewer.tsx`
2. File sent to `/api/assistants/files` (POST)
3. API uploads file to OpenAI
4. File added to vector store
5. Assistant can now search file content
6. File list updates automatically

### 4. Thread Management Flow
1. New thread created on page load
2. Thread saved to MongoDB `conversations` collection with name
3. Thread can be renamed, deleted, or shared
4. Shared threads become group conversations
5. Group threads poll for new messages every second
6. Threads persist in MongoDB database

### 5. Vector Store Management
1. On first file upload, vector store is created
2. Vector store attached to assistant
3. All files added to same vector store
4. Assistant searches across all files
5. Files are embedded for semantic search

---

## 🚀 How to Run

### Prerequisites
1. **Node.js** (v18 or higher)
2. **npm** or **yarn**
3. **OpenAI API Key**
4. **MongoDB Atlas Account** (free tier available)
5. **OpenAI Assistant ID** (optional, can create new one)

### Step-by-Step Setup

#### 1. Install Dependencies
```bash
npm install
```

#### 2. Configure Environment Variables
Create a `.env` file in the root directory:
```env
OPENAI_API_KEY=sk-proj-your-api-key-here
OPENAI_ASSISTANT_ID=asst_xxxxx  # Optional
MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/?retryWrites=true&w=majority
```

**To get your OpenAI API key:**
1. Go to https://platform.openai.com/api-keys
2. Create a new API key
3. Copy the key (starts with `sk-proj-` or `sk-`)

**To get your MongoDB connection string:**
1. Go to https://www.mongodb.com/cloud/atlas
2. Create a free cluster
3. Click "Connect" and choose "Connect your application"
4. Copy the connection string and replace `<password>` with your database password

**To get/create Assistant ID:**
1. Option A: Use the existing ID in `assistant-config.ts`
2. Option B: Create new assistant via API (POST to `/api/assistants`)
3. Option C: Create in OpenAI Dashboard and update `assistant-config.ts`

#### 3. Start Development Server
```bash
npm run dev
```

#### 4. Open in Browser
Navigate to: `http://localhost:3000`

### Production Build
```bash
npm run build
npm run start
```

---

## ⚙️ Configuration

### Environment Variables

#### Required
- `OPENAI_API_KEY` - Your OpenAI API key
- `MONGODB_URI` - Your MongoDB Atlas connection string

#### Optional
- `OPENAI_ASSISTANT_ID` - Override assistant ID from config file

### Assistant Configuration

Edit `app/assistant-config.ts` to change:
- Assistant ID
- Or set `OPENAI_ASSISTANT_ID` in `.env`

### Assistant Capabilities

The assistant is configured with:
1. **Code Interpreter** - Can write and execute Python code
2. **File Search** - Can search uploaded files
3. **Function Calling** - Can call custom functions (e.g., `get_weather`)

To modify capabilities, edit `app/api/assistants/route.ts` when creating assistant.

---

## ✨ Key Features

### 1. Real-Time Streaming
- Messages appear as they're generated
- No waiting for complete response
- Smooth user experience

### 2. File Search
- Upload files to vector store
- Assistant can search file contents
- Supports various file types (PDF, TXT, CSV, etc.)
- Semantic search across all files

### 3. Code Interpreter
- Assistant can write and run Python code
- Code output displayed in chat
- Useful for data analysis and calculations

### 4. Conversation History
- Multiple conversation threads
- Persistent storage in `userThreads.json`
- Rename and organize threads
- Load previous conversations

### 5. Group Chats
- Share thread ID with team members
- Multiple users can join same thread
- Real-time polling for new messages
- Collaborative conversations

### 6. File Management
- Upload multiple files
- View file status (processing, completed)
- Delete files when no longer needed
- Files accessible to all team members

### 7. Markdown Support
- Assistant responses render as markdown
- Supports code blocks, lists, links
- Professional formatting

---

## 🔍 Important Notes

### Vector Store
- Vector store is created automatically on first file upload
- All files share the same vector store
- Files are embedded for semantic search
- Processing may take time for large files

### Thread Management
- Threads are stored locally in `userThreads.json`
- Group threads are NOT deleted from OpenAI (preserved for team)
- Regular threads ARE deleted from OpenAI when removed
- Thread IDs are permanent and shareable

### API Costs
- OpenAI charges per API call
- File processing costs extra
- Longer conversations cost more (token usage)
- Code interpreter usage has additional costs

### File Limits
- Only 20 most recent files displayed
- File size limits depend on OpenAI's restrictions
- Supported formats: PDF, TXT, CSV, JSON, etc.

### Security
- API key should NEVER be committed to git
- Use `.env` file (add to `.gitignore`)
- Vector store files are accessible to all team members
- Thread IDs can be shared (be careful with sensitive data)

---

## 🐛 Troubleshooting

### API Key Not Working
- Check `.env` file exists
- Verify API key is correct
- Ensure no extra spaces in `.env`
- Restart dev server after changing `.env`

### Assistant Not Responding
- Check assistant ID is correct
- Verify assistant exists in OpenAI dashboard
- Check API key has sufficient credits
- Review browser console for errors

### Files Not Uploading
- Check file size limits
- Verify file format is supported
- Check network connection
- Review API response in browser dev tools

### Threads Not Loading
- Check `userThreads.json` exists and is valid JSON
- Verify file permissions
- Check API routes are working
- Review server logs

### Group Chat Not Updating
- Verify thread is marked as group (`isGroup: true`)
- Check polling interval (1 second)
- Verify other users have joined same thread ID
- Check browser console for errors

---

## 📚 Additional Resources

### OpenAI Documentation
- [Assistants API](https://platform.openai.com/docs/assistants/overview)
- [File Search](https://platform.openai.com/docs/assistants/tools/file-search)
- [Code Interpreter](https://platform.openai.com/docs/assistants/tools/code-interpreter)

### Next.js Documentation
- [App Router](https://nextjs.org/docs/app)
- [API Routes](https://nextjs.org/docs/app/building-your-application/routing/route-handlers)

### React Documentation
- [React Hooks](https://react.dev/reference/react)
- [State Management](https://react.dev/learn/state-a-components-memory)

---

## 🎓 Learning Points

This project demonstrates:
1. **Next.js App Router** - Modern Next.js routing
2. **API Routes** - Server-side endpoints
3. **Streaming** - Real-time data streaming
4. **File Handling** - Upload and manage files
5. **State Management** - React hooks and state
6. **TypeScript** - Type-safe development
7. **OpenAI Integration** - Assistant API usage
8. **Vector Stores** - Semantic search implementation

---

## 📝 Summary

This is a comprehensive AI-powered chat application with file search capabilities. It's designed for teams to collaborate, share knowledge, and interact with an AI assistant that can access uploaded documents. The application uses Next.js for the framework, OpenAI for AI capabilities, and local JSON storage for thread management.

Key strengths:
- Real-time streaming responses
- File search and management
- Conversation history
- Team collaboration features
- Code interpreter integration

The codebase is well-organized, uses TypeScript for type safety, and follows Next.js best practices. It's ready for development and can be extended with additional features as needed.

