"use client";

import React, { useState, useEffect, useRef, useMemo } from "react";
import useSWR from "swr";
import styles from "./chat.module.css";
import { AssistantStream } from "openai/lib/AssistantStream";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
// @ts-expect-error - no types for this yet
import { AssistantStreamEvent } from "openai/resources/beta/assistants/assistants";
import { RequiredActionFunctionToolCall } from "openai/resources/beta/threads/runs/runs";
import ThreadList from "./thread-list";
import SidebarAccount from "./sidebar-account";
import { API_ENDPOINTS, getMessageRequestBody, isPythonBackend } from "../config/api";
import { Plus, X, File as FileIcon, Loader2, Check, AlertCircle, SquarePen, Users } from "lucide-react";

// --- File attachment config (mirrors python_backend file_processing.SUPPORTED_EXTENSIONS) ---
const SUPPORTED_EXTENSIONS = [
  ".pdf", ".docx",
  ".xlsx", ".xls", ".csv",
  ".png", ".jpg", ".jpeg", ".tiff", ".tif",
  ".pptx",
];
// accept attribute for the hidden file input (per UI spec)
const FILE_ACCEPT = ".pdf,.docx,.xlsx,.xls,.csv,.pptx,.png,.jpg,.jpeg,.tiff";
const SUPPORTED_LABEL = "PDF, DOCX, XLSX, XLS, CSV, PPTX, PNG, JPG, JPEG, TIFF";
const MAX_UPLOAD_BYTES = 50 * 1024 * 1024; // 50 MB — must match backend

// Status polling: the /api/upload response returns immediately while the
// backend ingests in the background, so we poll until embeddings actually land.
const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 5 * 60 * 1000; // give up after 5 minutes

// Friendly labels for the optional stage text shown under an uploading chip.
const STAGE_LABELS: Record<string, string> = {
  extracting: "Extracting...",
  ocr: "OCR...",
  chunking: "Chunking...",
  embedding: "Embedding...",
  done: "Done",
};
const stageLabel = (stage?: string): string =>
  (stage && STAGE_LABELS[stage]) || "Processing...";

const getExt = (filename: string): string => {
  const i = filename.lastIndexOf(".");
  return i >= 0 ? filename.slice(i).toLowerCase() : "";
};
const isSupportedFile = (filename: string): boolean =>
  SUPPORTED_EXTENSIONS.includes(getExt(filename));

// One attachment chip in the input area, tracking its upload lifecycle.
type AttachedFile = {
  id: string;
  name: string;
  status: "uploading" | "ready" | "error";
  stage?: string; // backend ingest stage while processing (extracting/ocr/...)
  error?: string;
  settled?: boolean; // transient: after the success check, revert chip to the file icon
  // Thread this file was uploaded into. Chips belong to one conversation, so
  // switching threads clears them (the document itself stays with its thread).
  threadId?: string;
};

type MessageProps = {
  role: "user" | "assistant" | "code";
  text: string;
  annotations?: any[];
};

const UserMessage = ({ text }: { text: string }) => {
  return (
    <div className={styles.messageRow} style={{ justifyContent: 'flex-end' }}>
      <div className={styles.messageContent}>
        <div className={styles.messageLabel}>You</div>
        <div className={styles.userMessage}>{text}</div>
      </div>
    </div>
  );
};

const AssistantMessage = ({ text, annotations }: { text: string; annotations?: any[] }) => {
  // Replace citation annotations like 【6:0†source】 with actual filenames
  const replaceCitationsWithFilenames = (text: string, annotations?: any[]) => {
    // Regex to match citation patterns: 【number:number†source】
    const citationRegex = /【(\d+):(\d+)†([^】]+)】/g;
    
    // Create a map of citation text to filename
    const citationMap = new Map<string, string>();
    if (annotations && Array.isArray(annotations)) {
      annotations.forEach((annotation: any) => {
        if (annotation.type === 'file_citation' && annotation.file_citation) {
          const filename = annotation.file_citation.filename || 'Unknown File';
          citationMap.set(annotation.text, filename);
        }
      });
    }
    
    // Replace all citations with filename-based references
    const cleanedText = text.replace(citationRegex, (match) => {
      // Try to find the filename from annotations
      const filename = citationMap.get(match);
      
      if (filename) {
        // Return styled citation with filename
        return ` _(Source: ${filename})_ `;
      } else {
        // Fallback if filename not found
        return ` _(Source: Referenced File)_ `;
      }
    });

    return cleanedText;
  };

  const processedText = replaceCitationsWithFilenames(text, annotations);

  return (
    <div className={styles.messageRow} style={{ justifyContent: 'flex-start' }}>
      <div className={styles.messageContent}>
        <div className={styles.messageLabel}>AI Assistant</div>
        <div className={styles.assistantMessage}>
          <Markdown
            remarkPlugins={[remarkGfm, remarkMath]}
            rehypePlugins={[rehypeKatex]}
            components={{
              // Style emphasis/italic elements (our citations) with smaller gray text
              em: ({node, ...props}) => (
                <em style={{ 
                  color: '#6b7280', 
                  fontSize: '0.875em',
                  fontStyle: 'italic',
                  fontWeight: '500'
                }} {...props} />
              ),
              // Style paragraphs with spacing
              p: ({node, ...props}) => (
                <p style={{ 
                  marginTop: '0.75em', 
                  marginBottom: '0.75em',
                  lineHeight: '1.6'
                }} {...props} />
              ),
              // Style headings with proper spacing and sizing
              h1: ({node, ...props}) => (
                <h1 style={{ 
                  fontSize: '1.5em', 
                  fontWeight: 'bold', 
                  marginTop: '1em', 
                  marginBottom: '0.5em' 
                }} {...props} />
              ),
              h2: ({node, ...props}) => (
                <h2 style={{ 
                  fontSize: '1.3em', 
                  fontWeight: 'bold', 
                  marginTop: '1em', 
                  marginBottom: '0.5em' 
                }} {...props} />
              ),
              h3: ({node, ...props}) => (
                <h3 style={{ 
                  fontSize: '1.15em', 
                  fontWeight: 'bold', 
                  marginTop: '1em', 
                  marginBottom: '0.5em' 
                }} {...props} />
              ),
              h4: ({node, ...props}) => (
                <h4 style={{ 
                  fontSize: '1.05em', 
                  fontWeight: 'bold', 
                  marginTop: '0.75em', 
                  marginBottom: '0.5em' 
                }} {...props} />
              ),
              // Style code blocks nicely
              code: ({node, inline, ...props}: any) => 
                inline ? (
                  <code style={{ 
                    backgroundColor: '#f3f4f6',
                    padding: '0.2em 0.4em',
                    borderRadius: '3px',
                    fontSize: '0.875em',
                    fontFamily: 'monospace'
                  }} {...props} />
                ) : (
                  <code style={{ 
                    display: 'block',
                    backgroundColor: '#1e1e1e',
                    color: '#d4d4d4',
                    padding: '1em',
                    borderRadius: '6px',
                    overflowX: 'auto',
                    fontSize: '0.875em',
                    fontFamily: 'monospace',
                    marginTop: '0.75em',
                    marginBottom: '0.75em'
                  }} {...props} />
                ),
              // Style lists with better spacing
              ul: ({node, ...props}) => (
                <ul style={{ 
                  marginLeft: '1.5em', 
                  marginTop: '0.75em', 
                  marginBottom: '0.75em',
                  paddingLeft: '0.5em'
                }} {...props} />
              ),
              ol: ({node, ...props}) => (
                <ol style={{ 
                  marginLeft: '1.5em', 
                  marginTop: '0.75em', 
                  marginBottom: '0.75em',
                  paddingLeft: '0.5em'
                }} {...props} />
              ),
              li: ({node, ...props}) => (
                <li style={{ 
                  marginTop: '0.25em', 
                  marginBottom: '0.25em',
                  lineHeight: '1.6'
                }} {...props} />
              ),
              // Style strong/bold text
              strong: ({node, ...props}) => (
                <strong style={{ fontWeight: '600' }} {...props} />
              ),
              // Style tables
              table: ({node, ...props}) => (
                <div style={{ overflowX: 'auto', marginTop: '1em', marginBottom: '1em' }}>
                  <table style={{ 
                    borderCollapse: 'collapse',
                    width: '100%',
                    border: '1px solid #e5e7eb'
                  }} {...props} />
                </div>
              ),
              th: ({node, ...props}) => (
                <th style={{ 
                  border: '1px solid #e5e7eb',
                  padding: '0.5em',
                  backgroundColor: '#f9fafb',
                  fontWeight: 'bold'
                }} {...props} />
              ),
              td: ({node, ...props}) => (
                <td style={{ 
                  border: '1px solid #e5e7eb',
                  padding: '0.5em'
                }} {...props} />
              ),
              // Style blockquotes
              blockquote: ({node, ...props}) => (
                <blockquote style={{
                  borderLeft: '4px solid #e5e7eb',
                  paddingLeft: '1em',
                  marginLeft: '0',
                  marginTop: '0.75em',
                  marginBottom: '0.75em',
                  color: '#6b7280',
                  fontStyle: 'italic'
                }} {...props} />
              ),
              // Open all links in a new tab
              a: ({node, ...props}) => (
                <a
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{
                    color: '#2563eb',
                    textDecoration: 'underline',
                    cursor: 'pointer',
                  }}
                  {...props}
                />
              )
            }}
          >
            {processedText}
          </Markdown>
        </div>
      </div>
    </div>
  );
};

const CodeMessage = ({ text }: { text: string }) => {
  return (
    <div className={styles.messageRow} style={{ justifyContent: 'flex-start' }}>
      <div className={styles.messageContent}>
        <div className={styles.messageLabel}>Code Output</div>
        <div className={styles.codeMessage}>
          {text.split("\n").map((line, index) => (
            <div key={index}>
              <span>{`${index + 1}. `}</span>
              {line}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

const Message = ({ role, text, annotations }: MessageProps) => {
  switch (role) {
    case "user":
      return <UserMessage text={text} />;
    case "assistant":
      return <AssistantMessage text={text} annotations={annotations} />;
    case "code":
      return <CodeMessage text={text} />;
    default:
      return null;
  }
};

const STARTER_CARDS = [
  {
    title: "Explain a concept",
    example: "What is MICP and how does it work?",
    attach: false,
  },
  {
    title: "Compare methods",
    example: "EICP vs MICP for soil improvement",
    attach: false,
  },
  {
    title: "Understand a phenomenon",
    example: "What causes soil liquefaction?",
    attach: false,
  },
  {
    title: "Analyze a document",
    example: "Upload a paper and ask questions about it",
    attach: true,
  },
];

type WelcomeMessageProps = {
  onPromptSelect: (text: string) => void;
  onAttachClick: () => void;
};

const WelcomeMessage = ({ onPromptSelect, onAttachClick }: WelcomeMessageProps) => {
  return (
    <div className={styles.welcomeContainer}>
      <div className={styles.welcomeMessage}>
        <h1>GeoTech AI Assistant</h1>
        <p>
          Ask questions grounded in geotechnical research papers, or upload
          your own document to analyze.
        </p>
        <div className={styles.starterGrid}>
          {STARTER_CARDS.map((card) => (
            <button
              key={card.title}
              type="button"
              className={styles.starterCard}
              onClick={() =>
                card.attach ? onAttachClick() : onPromptSelect(card.example)
              }
            >
              <span className={styles.starterTitle}>{card.title}</span>
              <span className={styles.starterExample}>{card.example}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};

type ChatProps = {
  functionCallHandler?: (
    toolCall: RequiredActionFunctionToolCall
  ) => Promise<string>;
};

const Chat = ({
  functionCallHandler = () => Promise.resolve(""),
}: ChatProps) => {
  const [userInput, setUserInput] = useState("");
  const [messages, setMessages] = useState<MessageProps[]>([]);
  const [inputDisabled, setInputDisabled] = useState(false);
  const [threadId, setThreadId] = useState<string | null>("");
  // Mirrors threadId for code that mints a thread and then immediately uses it
  // in the SAME handler (attach -> upload), where the state closure is stale.
  const threadIdRef = useRef<string | null>("");
  // In-flight thread creation, so concurrent callers share one thread instead
  // of racing to create two.
  const threadCreationRef = useRef<Promise<string> | null>(null);
  // A thread that exists server-side but has no message yet (e.g. created by an
  // attach). Keeps the welcome screen up and drives one-time title generation.
  const [isDraftThread, setIsDraftThread] = useState(false);
  const awaitingFirstMessageRef = useRef(false);
  const [isGroupConversation, setIsGroupConversation] = useState(false);
  const threadListRef = useRef<any>(null);
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [joinThreadInput, setJoinThreadInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // --- File attachment UI state (replaces the removed right-hand file panel) ---
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  // True while any chip is still uploading/processing — used to block sending.
  const isUploading = attachedFiles.some((f) => f.status === "uploading");
  // Per-file status poll timers so each chip can be cancelled independently (e.g. via X).
  const pollTimersRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());

  // Clear any outstanding status polls when the chat unmounts.
  useEffect(() => {
    const timers = pollTimersRef.current;
    return () => {
      timers.forEach((t) => clearInterval(t));
      timers.clear();
    };
  }, []);

  // Keep the ref in sync for the paths that set threadId directly
  // (thread switch, New Chat). ensureThread sets both up front itself.
  useEffect(() => {
    threadIdRef.current = threadId;
  }, [threadId]);

  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  const [isNewThread, setIsNewThread] = useState(false);
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const lastMessageCountRef = useRef<number>(0);

  // --- Scroll tracking refs (never cause re-renders) ---
  // Updated on every scroll event via passive listener so the value is always fresh.
  const isAtBottomRef = useRef(true);
  // Set to true before a state update that MUST scroll (user send, thread switch).
  const shouldForceScrollRef = useRef(false);

  const getDefaultThreadName = () => {
    return new Date().toLocaleString();
  };

  // Scroll ONLY the .messages container by its own scrollTop — never
  // scrollIntoView, which walks the ancestor chain and (even past
  // overflow:hidden) drags the header/sidebar off-screen.
  const scrollToBottom = () => {
    const container = messagesContainerRef.current;
    if (container) {
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
    }
  };

  // Passive scroll listener keeps isAtBottomRef in sync without re-renders
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;
    const onScroll = () => {
      const threshold = 50;
      isAtBottomRef.current =
        container.scrollHeight - container.scrollTop - container.clientHeight <= threshold;
    };
    container.addEventListener("scroll", onScroll, { passive: true });
    return () => container.removeEventListener("scroll", onScroll);
  }, [threadId]);

  // MutationObserver auto-scroll: watches the DOM for any layout changes
  // (new messages, streaming text, late Markdown/image renders) and scrolls
  // only when the user is already at the bottom or a forced scroll is pending.
  // If the user has scrolled up, the observer skips scrolling entirely.
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    let rafId: number | null = null;

    const observer = new MutationObserver(() => {
      if (rafId !== null) return;

      rafId = requestAnimationFrame(() => {
        rafId = null;
        if (shouldForceScrollRef.current) {
          shouldForceScrollRef.current = false;
          scrollToBottom();
          return;
        }
        if (isAtBottomRef.current) {
          scrollToBottom();
        }
      });
    });

    observer.observe(container, {
      childList: true,
      subtree: true,
      characterData: true,
    });

    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId);
      observer.disconnect();
    };
  }, [threadId]);


  // SWR fetcher function for message history
  const fetcher = async (url: string) => {
    const res = await fetch(url, { credentials: "include" });
    if (!res.ok) throw new Error('Failed to fetch messages');
    return res.json();
  };

  // SWR polling for real-time message updates (for group conversations)
  const { data: messageData } = useSWR(
    isGroupConversation && threadId ? API_ENDPOINTS.getChatHistory(threadId) : null,
    fetcher,
    {
      refreshInterval: 2000,
      revalidateOnFocus: true,
      revalidateOnReconnect: true,
    }
  );

  // Update messages when SWR fetches new data (ONLY for group conversations)
  useEffect(() => {
    if (messageData?.messages && isGroupConversation && threadId) {
      const parsed = messageData.messages.map((msg: any) => {
        let text = msg.content?.[0]?.text?.value || msg.content || '';
        const sources = msg.sources || [];
        if (msg.role === 'assistant' && sources.length > 0 && !text.includes('**Sources:**')) {
          text += "\n\n**Sources:**\n";
          sources.forEach((source: any, index: number) => {
            if (typeof source === "object" && source !== null && source.title && source.url) {
              text += `${index + 1}. [${source.title}](${source.url})\n`;
            } else if (typeof source === "string") {
              try {
                const parsed = JSON.parse(source);
                if (parsed.title && parsed.url) {
                  text += `${index + 1}. [${parsed.title}](${parsed.url})\n`;
                } else {
                  text += `${index + 1}. ${source}\n`;
                }
              } catch {
                text += `${index + 1}. ${source}\n`;
              }
            }
          });
        }
        return {
          role: msg.role,
          text,
          annotations: msg.content?.[0]?.text?.annotations || []
        };
      });
      if (parsed.length > lastMessageCountRef.current) {
        console.log(`[SWR] Group update: ${parsed.length} messages (had ${lastMessageCountRef.current})`);
        setMessages(parsed);
        lastMessageCountRef.current = parsed.length;
      }
    }
  }, [messageData, isGroupConversation, threadId]);

  // REMOVED: Auto-select behavior
  // On initial load, threadId remains null to show welcome state
  // User must click a thread or start typing to create new thread
  useEffect(() => {
    // Only load thread list, don't auto-select
    if (threadListRef.current) {
      threadListRef.current.fetchThreads();
    }
  }, []); 

  const generateAndUpdateTitle = async (firstMessage: string, targetThreadId: string) => {
    try {
      if (!targetThreadId) {
        console.error("Cannot generate title: threadId is null or undefined");
        return;
      }

      const titleEndpoint = API_ENDPOINTS.generateTitle(targetThreadId);
      const response = await fetch(titleEndpoint, {
        credentials: "include",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: firstMessage,
        }),
      });

      if (!response.ok) {
        console.error("Failed to generate title");
        return;
      }

      const data = await response.json();
      const title = data.title;

      // Update thread name in history
      const updateEndpoint = API_ENDPOINTS.updateThread();
      await fetch(updateEndpoint, {
        credentials: "include",
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          threadId: targetThreadId,
          newName: title,
        }),
      });

      // Refresh thread list to show updated title
      if (threadListRef.current) {
        await threadListRef.current.fetchThreads();
      }
    } catch (error) {
      console.error("Error generating title:", error);
    }
  };

  const handleSSEStream = async (readableStream: ReadableStream) => {
    const processSSEMessage = async (message: string) => {
      if (!message.trim()) return;

      const lines = message.split('\n');
      let eventType = '';
      let data = '';

      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventType = line.substring(6).trim();
        } else if (line.startsWith('data:')) {
          data = line.substring(5).trim();
        }
      }

      if (!data) return;

      if (data === '[DONE]') {
        console.log("Stream completed with [DONE] signal");
        setInputDisabled(false);
        return;
      }

      try {
        const parsed = JSON.parse(data);
        console.log("📩 SSE Event:", eventType, "Data:", parsed);

        if (eventType === 'thread.message.created' || parsed.object === 'thread.message') {
          if (parsed.role === 'assistant') {
            appendMessage("assistant", "");
          }
        } else if (eventType === 'thread.message.delta' || parsed.object === 'thread.message.delta') {
          if (parsed.delta?.content) {
            for (const content of parsed.delta.content) {
              if (content.type === 'text' && content.text?.value) {
                appendToLastMessage(content.text.value);
              }
            }
          }
        } else if (eventType === 'thread.run.completed' || parsed.status === 'completed') {
          console.log("Run completed");
          setInputDisabled(false);
        } else if (eventType === 'thread.run.failed' || parsed.status === 'failed') {
          console.error("Run failed:", parsed);
          setInputDisabled(false);
          const errorMsg = parsed.last_error?.message || "The assistant run failed. Please try again.";
          appendMessage("assistant", `\n\n[Error: ${errorMsg}]`);
        } else if (eventType === 'thread.run.requires_action') {
          if (parsed.required_action?.type === 'submit_tool_outputs') {
            const toolCalls = parsed.required_action.submit_tool_outputs.tool_calls;
            const toolCallOutputs = await Promise.all(
              toolCalls.map(async (toolCall: RequiredActionFunctionToolCall) => {
                const result = await functionCallHandler(toolCall);
                return { output: result, tool_call_id: toolCall.id };
              })
            );
            setInputDisabled(true);
            await submitActionResult(parsed.id, toolCallOutputs);
          }
        } else if (parsed.error) {
          console.error("Stream error:", parsed.error);
          appendMessage("assistant", `\n\n[Error: ${parsed.error}]`);
          setInputDisabled(false);
        }
      } catch (parseError) {
        console.error("Error parsing SSE data:", parseError, "Data:", data);
      }
    };

    try {
      const reader = readableStream.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();

        if (done) {
          // Flush the decoder and process any remaining buffer data
          buffer += decoder.decode();
          const remaining = buffer.split('\n\n');
          for (const msg of remaining) {
            await processSSEMessage(msg);
          }
          console.log("Stream ended");
          setInputDisabled(false);
          break;
        }

        buffer += decoder.decode(value, { stream: true });

        const messages = buffer.split('\n\n');
        buffer = messages.pop() || '';

        for (const message of messages) {
          await processSSEMessage(message);
        }
      }
    } catch (error) {
      console.error("Error reading SSE stream:", error);
      appendMessage("assistant", `\n\n[Error: ${error.message || "Failed to read stream"}]`);
      setInputDisabled(false);
    }
  };

  const sendMessage = async (text: string, targetThreadId: string | null = null) => {
    const actualThreadId = targetThreadId || threadId;

    try {
      // Get API endpoint based on configuration (Python or Next.js)
      const endpoint = API_ENDPOINTS.sendMessage(actualThreadId);
      const requestBody = getMessageRequestBody(text, actualThreadId);
      
      console.log("📤 Sending message to:", endpoint);
      console.log("📦 Request body:", JSON.stringify(requestBody, null, 2));
      console.log("🆔 Thread ID:", actualThreadId);
      
      const response = await fetch(endpoint, {
        credentials: "include",
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });
      
      console.log("📨 Response status:", response.status);

      if (!response.ok) {
        let errorMessage = `Failed to send message (Status: ${response.status})`;
        // Read the body exactly ONCE. A Response stream can only be consumed
        // once, so we take the raw text and then try to parse it as JSON,
        // instead of calling .json() and .text() on the same response (which
        // throws "body stream already read" when the error body isn't JSON,
        // e.g. an HTML 5xx page from the proxy).
        const errorText = await response.text();
        try {
          const errorData = JSON.parse(errorText);
          // FastAPI errors use { detail }; some routes use { error }.
          errorMessage = errorData.detail || errorData.error || errorMessage;
          if (errorData.details) {
            console.error("Error details:", errorData.details);
          }
        } catch {
          errorMessage = errorText || errorMessage;
        }
        console.error(`Failed to send message. Status: ${response.status}`);
        console.error(`Response: ${errorMessage}`);
        appendMessage("assistant", `\n\n[Error: ${errorMessage}]`);
        setInputDisabled(false);
        return;
      }

      // Parse JSON response from Python backend
      const data = await response.json();
      console.log("📦 Response data:", data);
      
      // Extract answer and sources
      const answer = data.answer || "";
      const sources = data.sources || [];
      
      // Build the complete response with clickable source links
      let fullResponse = answer;
      
      if (sources && sources.length > 0) {
        fullResponse += "\n\n**Sources:**\n";
        sources.forEach((source: any, index: number) => {
          if (typeof source === "object" && source !== null && source.title && source.url) {
            fullResponse += `${index + 1}. [${source.title}](${source.url})\n`;
          } else if (typeof source === "string") {
            try {
              const parsed = JSON.parse(source);
              if (parsed.title && parsed.url) {
                fullResponse += `${index + 1}. [${parsed.title}](${parsed.url})\n`;
              } else {
                fullResponse += `${index + 1}. ${source}\n`;
              }
            } catch {
              fullResponse += `${index + 1}. ${source}\n`;
            }
          } else {
            fullResponse += `${index + 1}. ${String(source)}\n`;
          }
        });
      }
      
      console.log("✅ Answer extracted:", answer.substring(0, 100) + "...");
      console.log("📚 Sources:", sources);
      
      appendMessage("assistant", fullResponse);
      setInputDisabled(false);

      // Title generation is owned by handleSubmit (the only caller), which knows
      // whether this is the thread's first turn. Doing it here too fired the
      // title LLM twice for a thread started from "New Chat".
    } catch (error) {
      console.error("Error sending message:", error);
      appendMessage("assistant", `\n\n[Error: ${error.message || "Failed to send message"}]`);
      setInputDisabled(false);
    }
  };

  const submitActionResult = async (runId: string, toolCallOutputs: any[]) => {
    try {
      const response = await fetch(
        API_ENDPOINTS.submitActions(threadId),
        {
          credentials: "include",
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            runId: runId,
            toolCallOutputs: toolCallOutputs,
          }),
        }
      );

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`Failed to submit action. Status: ${response.status}`);
        console.error(`Response: ${errorText}`);
        appendMessage("assistant", `\n\n[Error: Failed to submit action. Status ${response.status}]`);
        setInputDisabled(false);
        return;
      }

      if (!response.body) {
        console.error("Response body is null");
        appendMessage("assistant", "\n\n[Error: No response from server]");
        setInputDisabled(false);
        return;
      }

      const stream = AssistantStream.fromReadableStream(response.body);
      handleReadableStream(stream);
    } catch (error) {
      console.error("Error submitting action:", error);
      appendMessage("assistant", `\n\n[Error: ${error.message || "Failed to submit action"}]`);
      setInputDisabled(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Handle Enter key behavior
    if (e.key === 'Enter') {
      if (e.shiftKey) {
        // Shift+Enter: Allow default behavior (new line)
        return;
      } else {
        // Enter (without Shift): Submit the message
        e.preventDefault(); // Prevent newline
        
        // Don't submit if already processing, uploading, or input is empty
        if (inputDisabled || isUploading || !userInput.trim()) {
          return;
        }
        
        // Trigger form submission
        handleSubmit(e as any);
      }
    }
  };

  // Threads are created lazily — nothing exists server-side until the user
  // commits something to the conversation. Both the composer's first Send AND
  // the attach button need a real thread id (an upload without one is stored as
  // a plain user_upload and is invisible to thread-scoped retrieval), so the
  // minting lives here: returns the current thread when there is one, otherwise
  // creates it (id + sidebar history row) and marks it as awaiting its first
  // message. threadCreationRef collapses concurrent callers onto one creation.
  const ensureThread = async (): Promise<string> => {
    if (threadIdRef.current) return threadIdRef.current;
    if (threadCreationRef.current) return threadCreationRef.current;

    const creation = (async () => {
      const res = await fetch(API_ENDPOINTS.createThread(), {
        credentials: "include",
        method: "POST",
      });
      if (!res.ok) {
        throw new Error(`Failed to create thread (status ${res.status})`);
      }
      const data = await res.json();
      const newThreadId = data.threadId;
      if (!newThreadId) {
        throw new Error("Server did not return a thread id");
      }

      // Publish the id synchronously so the rest of THIS handler (e.g. the
      // upload that triggered the creation) sees it without a re-render.
      threadIdRef.current = newThreadId;
      setThreadId(newThreadId);
      setIsNewThread(true);
      awaitingFirstMessageRef.current = true;
      // Attach-before-typing must not swap the welcome screen for an empty
      // message pane; the thread only "starts" once a message is sent.
      setIsDraftThread(true);

      // Sidebar row. Registering it here (rather than at first message) keeps an
      // attach-first thread visible and deletable — deleting a thread cascades
      // to its thread_upload documents, so an abandoned upload is never orphaned.
      await fetch(API_ENDPOINTS.createThreadHistory(), {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId: newThreadId,
          name: getDefaultThreadName(),
          isGroup: false,
        }),
      });

      if (threadListRef.current) {
        await threadListRef.current.fetchThreads();
      }

      return newThreadId;
    })();

    threadCreationRef.current = creation;
    try {
      return await creation;
    } finally {
      threadCreationRef.current = null;
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userInput.trim() || inputDisabled || isUploading) return;

    const messageText = userInput;
    setUserInput("");
    setInputDisabled(true);

    // No-op when the thread already exists — whether the user typed into an
    // open conversation or an attach already minted the thread.
    let activeThreadId: string;
    try {
      activeThreadId = await ensureThread();
    } catch (error) {
      console.error("Failed to create thread:", error);
      setUserInput(messageText); // give the text back rather than losing it
      setInputDisabled(false);
      return;
    }

    // First turn of this thread, whether it was minted just now or earlier by an
    // attach. Drives title generation exactly once.
    const isFirstTurn = awaitingFirstMessageRef.current;
    awaitingFirstMessageRef.current = false;
    setIsDraftThread(false);

    shouldForceScrollRef.current = true;
    setMessages((prevMessages) => {
      const newMessages: MessageProps[] = [
        ...prevMessages,
        { role: "user" as const, text: messageText },
      ];
      lastMessageCountRef.current = newMessages.length;
      return newMessages;
    });

    await sendMessage(messageText, activeThreadId);

    if (isFirstTurn) {
      // Best-effort: generateAndUpdateTitle swallows its own errors and
      // refreshes the sidebar itself.
      await generateAndUpdateTitle(messageText, activeThreadId);
      setIsNewThread(false);
    }
  };

  /* Stream Event Handlers */

  // textCreated - create new assistant message
  const handleTextCreated = () => {
    appendMessage("assistant", "");
  };

  // textDelta - append text to last assistant message
  const handleTextDelta = (delta: any) => {
    if (delta.value != null) {
      appendToLastMessage(delta.value);
    }
    if (delta.annotations != null) {
      annotateLastMessage(delta.annotations);
    }
  };

  // imageFileDone - show image in chat
  const handleImageFileDone = (image: any) => {
    const backendUrl = ''; // same-origin; /api/files/* is rewritten to FastAPI
    appendToLastMessage(`\n![${image.file_id}](${backendUrl}/api/files/${image.file_id})\n`);
  }

  // toolCallCreated - log new tool call
  const toolCallCreated = (toolCall: any) => {
    if (toolCall.type != "code_interpreter") return;
    appendMessage("code", "");
  };

  // toolCallDelta - log delta and snapshot for the tool call
  const toolCallDelta = (delta: any, snapshot: any) => {
    if (delta.type != "code_interpreter") return;
    if (!delta.code_interpreter.input) return;
    appendToLastMessage(delta.code_interpreter.input);
  };

  // handleRequiresAction - handle function call
  const handleRequiresAction = async (
    event: AssistantStreamEvent.ThreadRunRequiresAction
  ) => {
    const runId = event.data.id;
    const toolCalls = event.data.required_action.submit_tool_outputs.tool_calls;
    // loop over tool calls and call function handler
    const toolCallOutputs = await Promise.all(
      toolCalls.map(async (toolCall) => {
        const result = await functionCallHandler(toolCall);
        return { output: result, tool_call_id: toolCall.id };
      })
    );
    setInputDisabled(true);
    submitActionResult(runId, toolCallOutputs);
  };

  // handleRunCompleted - re-enable the input form
  const handleRunCompleted = () => {
    setInputDisabled(false);
  };

  // handleRunFailed - handle failed runs
  const handleRunFailed = (event) => {
    console.error("Run failed:", event);
    setInputDisabled(false);
    if (event.data?.last_error) {
      appendMessage("assistant", `\n\n[Error: ${event.data.last_error.message || "The assistant run failed. Please try again."}]`);
    } else {
      appendMessage("assistant", "\n\n[Error: The assistant run failed. Please try again.]");
    }
  };

  // handleRunCancelled - handle cancelled runs
  const handleRunCancelled = () => {
    console.warn("Run was cancelled");
    setInputDisabled(false);
  };

  const handleReadableStream = (stream: AssistantStream) => {
    // Add error handler to catch stream errors including "Final run has not been received"
    stream.on("error", (error) => {
      console.error("Stream error:", error);
      setInputDisabled(false);
      // Show user-friendly error message
      if (error.message && error.message.includes("Final run has not been received")) {
        appendMessage("assistant", "\n\n[Error: The connection was interrupted. Please try sending your message again.]");
      } else {
        appendMessage("assistant", `\n\n[Error: ${error.message || "An error occurred. Please try again."}]`);
      }
    });

    // messages
    stream.on("textCreated", handleTextCreated);
    stream.on("textDelta", handleTextDelta);

    // image
    stream.on("imageFileDone", handleImageFileDone);

    // code interpreter
    stream.on("toolCallCreated", toolCallCreated);
    stream.on("toolCallDelta", toolCallDelta);

    // events without helpers yet (e.g. requires_action and run.done)
    stream.on("event", (event) => {
      if (event.event === "thread.run.requires_action")
        handleRequiresAction(event);
      if (event.event === "thread.run.completed") handleRunCompleted();
      if (event.event === "thread.run.failed") handleRunFailed(event);
      if (event.event === "thread.run.cancelled") handleRunCancelled();
    });

    // Handle stream end - safety net to re-enable input if completion event is missed
    stream.on("end", () => {
      console.log("Stream ended");
      // Small delay to allow completion event to fire first
      setTimeout(() => {
        setInputDisabled(false);
      }, 100);
    });
  };

  /*
    =======================
    === Utility Helpers ===
    =======================
  */

  const appendToLastMessage = (text: string) => {
    setMessages((prevMessages) => {
      const lastMessage = prevMessages[prevMessages.length - 1];
      const updatedLastMessage = {
        ...lastMessage,
        text: lastMessage.text + text,
      };
      return [...prevMessages.slice(0, -1), updatedLastMessage];
    });
  };

  const appendMessage = (role: "user" | "assistant" | "code", text: string) => {
    setMessages((prevMessages) => {
      const newMessages = [...prevMessages, { role, text }];
      lastMessageCountRef.current = newMessages.length;
      return newMessages;
    });
  };

  const annotateLastMessage = (annotations: any[]) => {
    setMessages((prevMessages) => {
      const lastMessage = prevMessages[prevMessages.length - 1];
      const updatedLastMessage = {
        ...lastMessage,
      };
      annotations.forEach((annotation) => {
        if (annotation.type === 'file_path') {
          const backendUrl = ''; // same-origin; /api/files/* is rewritten to FastAPI
          const fullPath = `${backendUrl}/api/files/${annotation.file_path.file_id}`;
          
          updatedLastMessage.text = updatedLastMessage.text.replaceAll(
            annotation.text,
            fullPath
          );
        }
      })
      return [...prevMessages.slice(0, -1), updatedLastMessage];
    });
  }

  // Use a ref to track the currently loading thread to prevent race conditions
  const loadingThreadRef = useRef<string | null>(null);
  
  const loadThread = async (targetThreadId: string, isInitialLoad = true) => {
    try {
      // Track which thread we're loading to prevent stale updates
      loadingThreadRef.current = targetThreadId;
      
      if (isInitialLoad) {
        setThreadId(targetThreadId);
      }
      
      // Use the chat history endpoint to retrieve messages from MongoDB
      const endpoint = API_ENDPOINTS.getChatHistory(targetThreadId);
      console.log(`[LOAD] Fetching history for thread: ${targetThreadId}`);
      console.log(`[LOAD] URL: ${endpoint}`);
      const response = await fetch(endpoint, {
        credentials: "include",
        cache: 'no-store',
        headers: { 'Cache-Control': 'no-cache' }
      });
      
      // CRITICAL: If user switched to another thread while we were fetching,
      // discard this response to prevent showing wrong messages
      if (loadingThreadRef.current !== targetThreadId && isInitialLoad) {
        console.log(`[LOAD] Discarding stale response for ${targetThreadId} (now loading ${loadingThreadRef.current})`);
        return;
      }
      
      if (!response.ok) {
        console.warn(`[LOAD] Failed to fetch history: ${response.status}`);
        setMessages([]);
        setIsNewThread(true);
        return;
      }
      
      const data = await response.json();
      const msgCount = data.count || data.messages?.length || 0;
      console.log(`[LOAD] Received ${msgCount} messages for thread ${targetThreadId}`);
      
      // Log first message content for debugging
      if (data.messages && data.messages.length > 0) {
        const firstMsg = data.messages[0];
        console.log(`[LOAD] First message role: ${firstMsg.role}, content preview: ${(firstMsg.content || '').substring(0, 80)}`);
      }
      
      // CRITICAL: Double-check we're still on the same thread before updating state
      if (loadingThreadRef.current !== targetThreadId && isInitialLoad) {
        console.log(`[LOAD] DISCARDING stale data for ${targetThreadId} (now loading ${loadingThreadRef.current})`);
        return;
      }
      
      // Parse messages - handle both old format (nested content) and new format (direct content)
      const newMessages = (data.messages || []).map((msg: any) => {
        let text = msg.content?.[0]?.text?.value || msg.content || '';
        const sources = msg.sources || [];
        if (msg.role === 'assistant' && sources.length > 0 && !text.includes('**Sources:**')) {
          text += "\n\n**Sources:**\n";
          sources.forEach((source: any, index: number) => {
            if (typeof source === "object" && source !== null && source.title && source.url) {
              text += `${index + 1}. [${source.title}](${source.url})\n`;
            } else if (typeof source === "string") {
              try {
                const parsed = JSON.parse(source);
                if (parsed.title && parsed.url) {
                  text += `${index + 1}. [${parsed.title}](${parsed.url})\n`;
                } else {
                  text += `${index + 1}. ${source}\n`;
                }
              } catch {
                text += `${index + 1}. ${source}\n`;
              }
            }
          });
        }
        return {
          role: msg.role,
          text,
          annotations: msg.content?.[0]?.text?.annotations || []
        };
      });
      
      console.log(`[LOAD] Parsed ${newMessages.length} messages for thread: ${targetThreadId}`);
      
      if (isInitialLoad) {
        // On initial load (thread switch), always use backend state
        setMessages(newMessages);
        lastMessageCountRef.current = newMessages.length;
        setIsNewThread(newMessages.length === 0);
        if (newMessages.length > 0) {
          shouldForceScrollRef.current = true;
        }
      } else {
        // On polling, only update if backend has MORE messages than local state
        if (newMessages.length > lastMessageCountRef.current) {
          console.log(`[LOAD] Updating: backend has ${newMessages.length}, local had ${lastMessageCountRef.current}`);
          setMessages(newMessages);
          lastMessageCountRef.current = newMessages.length;
          setIsNewThread(newMessages.length === 0);
        }
      }
    } catch (error) {
      console.error('Failed loading history conversation:', error);
      if (isInitialLoad) {
        setMessages([]);
        setIsNewThread(true);
        lastMessageCountRef.current = 0;
      }
    }
  }
  
  // Polling function to check for new messages
  const pollForNewMessages = async (threadId: string) => {
    if (!threadId) {
      return;
    }
    
    try {
      await loadThread(threadId, false);
    } catch (error) {
      console.error('Polling error:', error);
    }
  }
  
  // Effect to manage polling for real-time updates
  // Only poll when actively waiting for AI response to reduce server load
  useEffect(() => {
    // Clear any existing polling interval
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    
    // Only start polling when waiting for AI response (inputDisabled = true)
    // This prevents constant polling when idle
    if (threadId && !isNewThread && inputDisabled) {
      console.log(`🔄 Starting polling while waiting for AI response: ${threadId}`);
      
      // Poll every 2 seconds only while waiting for response
      pollingIntervalRef.current = setInterval(() => {
        pollForNewMessages(threadId);
      }, 2000);
    }
    
    // Cleanup function
    return () => {
      if (pollingIntervalRef.current) {
        console.log(`⏹️ Stopping polling for thread: ${threadId}`);
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, [threadId, isNewThread, inputDisabled]); // Added inputDisabled dependency
  const createNewThread = () => {
    // Simply reset to welcome state
    // Thread will be created when the user sends the first message OR attaches
    // the first file, whichever comes first.
    setThreadId(null);
    threadIdRef.current = null;
    setMessages([]);
    setIsGroupConversation(false);
    setIsNewThread(true);
    setIsDraftThread(false);
    awaitingFirstMessageRef.current = false;
    clearAttachedFiles();
    setUserInput("");
    lastMessageCountRef.current = 0;
  };

  const handleThreadSelect = (selectedThreadId: string | null, isGroup: boolean) => {
    // CRITICAL: Stop any active polling IMMEDIATELY before changing state
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    
    // Attachments are per-conversation; never carry chips across a switch.
    clearAttachedFiles();
    setIsDraftThread(false);
    awaitingFirstMessageRef.current = false;

    if (selectedThreadId === null) {
      // Show welcome screen - clear the current thread
      setThreadId("");
      threadIdRef.current = "";
      setMessages([]);
      setIsGroupConversation(false);
      setIsNewThread(false);
      lastMessageCountRef.current = 0;
    } else {
      console.log(`[SWITCH] ========================================`);
      console.log(`[SWITCH] Switching to thread: ${selectedThreadId}`);
      console.log(`[SWITCH] isGroup: ${isGroup}`);
      console.log(`[SWITCH] Previous threadId: ${threadId}`);
      console.log(`[SWITCH] ========================================`);
      
      // 1. Clear messages and reset ALL state
      setMessages([]);
      lastMessageCountRef.current = 0;
      setInputDisabled(false);
      setIsGroupConversation(isGroup);
      setIsNewThread(false);
      
      // 2. Set thread ID
      setThreadId(selectedThreadId);
      threadIdRef.current = selectedThreadId;

      // 3. Load messages directly (passing selectedThreadId to avoid stale state)
      loadThread(selectedThreadId, true);
    }
  };

  const handleJoinTeam = async () => {
    if (!joinThreadInput.trim()) return;
    
    try {
      setShowJoinModal(false);
      setJoinThreadInput('');
      // Load the shared thread directly without saving to personal sidebar history
      handleThreadSelect(joinThreadInput, true);
    } catch (error) {
      console.error('Error joining team chat:', error);
    }
  };

  // Patch a single chip by id. Uses functional setState so concurrent uploads
  // don't clobber each other; if the chip was removed mid-flight this no-ops.
  const updateAttachedFile = (id: string, patch: Partial<AttachedFile>) => {
    setAttachedFiles((prev) =>
      prev.map((f) => (f.id === id ? { ...f, ...patch } : f))
    );
  };

  // Stop and forget a file's status poll (if any).
  const stopPolling = (id: string) => {
    const timer = pollTimersRef.current.get(id);
    if (timer !== undefined) {
      clearInterval(timer);
      pollTimersRef.current.delete(id);
    }
  };

  // Poll GET /api/upload/status until the backend reports the file is fully
  // ingested (embeddings written) or errored. Each file polls independently so
  // removing its chip via X cancels just that cycle.
  const startPolling = (id: string, filename: string, forThreadId: string) => {
    const deadline = Date.now() + POLL_TIMEOUT_MS;

    const poll = async () => {
      // Safety timeout — don't spin forever if ingest never resolves.
      if (Date.now() > deadline) {
        stopPolling(id);
        updateAttachedFile(id, { status: "error", error: "Processing timed out" });
        return;
      }

      try {
        // Scoped to the thread: the same filename can be attached to more than
        // one conversation, and an unscoped lookup would report the other
        // upload's status.
        const resp = await fetch(API_ENDPOINTS.uploadStatus(filename, forThreadId), {
          credentials: "include",
        });
        if (!resp.ok) return; // transient server hiccup — keep polling until timeout
        const data = await resp.json();

        if (data.status === "ready") {
          stopPolling(id);
          updateAttachedFile(id, { status: "ready", stage: undefined });
          // Settle the chip back to the calm file icon after the success check.
          setTimeout(() => updateAttachedFile(id, { settled: true }), 1500);
        } else if (data.status === "error") {
          stopPolling(id);
          updateAttachedFile(id, { status: "error", error: data.error || "Processing failed" });
        } else {
          // Still processing — reflect the current stage on the chip.
          updateAttachedFile(id, { stage: data.stage });
        }
      } catch (e) {
        // Network blip — leave the chip spinning and try again next tick.
        console.warn(`Status poll failed for ${filename}:`, e);
      }
    };

    poll(); // check immediately, then on an interval
    const timer = setInterval(poll, POLL_INTERVAL_MS);
    pollTimersRef.current.set(id, timer);
  };

  // Uploads one file INTO a thread and reflects the outcome on its chip.
  //
  // threadId is what makes the file a thread document: with it the backend
  // stores the upload as category "thread_upload" tagged with this thread, which
  // is what thread_has_documents() looks for and what lets the router pick
  // THREAD_DOC. Without it the file lands as a generic user_upload and the
  // assistant can never see it as "the file you just attached", so we never
  // upload without one.
  const uploadAttachedFile = async (id: string, file: File, forThreadId: string) => {
    const data = new FormData();
    data.append("file", file); // "file" (singular) for /upload endpoint
    data.append("threadId", forThreadId);

    try {
      const resp = await fetch(API_ENDPOINTS.uploadFile(), {
        credentials: "include",
        method: "POST",
        body: data,
      });

      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ detail: "Unknown error" }));
        console.error(`Failed to upload ${file.name}:`, errorData);
        updateAttachedFile(id, {
          status: "error",
          error: errorData.detail || `Upload failed (status ${resp.status})`,
        });
        return;
      }

      const result = await resp.json();
      const hasProcessingError = result.status === "processing_failed" || result.error;

      if (hasProcessingError) {
        updateAttachedFile(id, {
          status: "error",
          error: result.error || "Processing issue — file may be partially indexed.",
        });
        return;
      }

      // Upload accepted. The backend now ingests (extract/OCR/chunk/embed) in a
      // background task, so the chip stays "uploading" and we poll the status
      // endpoint until embeddings actually land.
      updateAttachedFile(id, { stage: result.stage || "processing" });
      startPolling(id, file.name, forThreadId);
    } catch (error: any) {
      console.error(`Error uploading ${file.name}:`, error);
      updateAttachedFile(id, {
        status: "error",
        error: error?.message || "Upload failed. Please try again.",
      });
    }
  };

  // Triggered by the hidden file <input> behind the "+" button.
  // Valid files render immediately as "uploading" chips; the thread is created
  // first (attaching may be the user's very first action in a new chat, before
  // any message exists), then each upload resolves its own chip independently.
  const handleFileAttach = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files;
    if (!selectedFiles || selectedFiles.length === 0) return;

    // Client-side pre-flight: filter out unsupported types and oversize files.
    const valid: File[] = [];
    for (let i = 0; i < selectedFiles.length; i++) {
      const f = selectedFiles[i];
      if (!isSupportedFile(f.name)) {
        alert(`"${f.name}" is not a supported file type.\n\nSupported: ${SUPPORTED_LABEL}`);
        continue;
      }
      if (f.size > MAX_UPLOAD_BYTES) {
        alert(`"${f.name}" is ${(f.size / 1024 / 1024).toFixed(1)} MB which exceeds the 50 MB limit.`);
        continue;
      }
      valid.push(f);
    }

    // Reset the input so the same file can be re-selected.
    event.target.value = "";

    if (valid.length === 0) return;

    // Assign each valid file a stable id and render its chip right away.
    const entries = valid.map((file, i) => ({
      id:
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : `${Date.now()}-${i}-${file.name}`,
      file,
    }));

    setAttachedFiles((prev) => [
      ...prev,
      ...entries.map(({ id, file }) => ({
        id,
        name: file.name,
        status: "uploading" as const,
      })),
    ]);

    // A file is always uploaded INTO a thread, so mint one if this attach came
    // before the first message. Cheap and idempotent when a thread is open.
    let forThreadId: string;
    try {
      forThreadId = await ensureThread();
    } catch (error) {
      console.error("Failed to create a thread for the attachment:", error);
      entries.forEach(({ id }) =>
        updateAttachedFile(id, {
          status: "error",
          error: "Couldn't start a conversation for this file. Please try again.",
        })
      );
      return;
    }

    // Fire uploads concurrently; each resolves its own chip by id.
    entries.forEach(({ id, file }) => {
      updateAttachedFile(id, { threadId: forThreadId });
      uploadAttachedFile(id, file, forThreadId);
    });
  };

  // Removes an attachment chip from the input (UI only). Cancels its status
  // poll; any in-flight upload request resolves in the background and is ignored.
  const removeAttachedFile = (id: string) => {
    stopPolling(id);
    setAttachedFiles((prev) => prev.filter((f) => f.id !== id));
  };

  // Drops every chip and its poll. Chips belong to one conversation, so leaving
  // thread A's attachments visible in thread B would misrepresent what the
  // assistant can actually see (the documents stay with their own thread).
  const clearAttachedFiles = () => {
    pollTimersRef.current.forEach((t) => clearInterval(t));
    pollTimersRef.current.clear();
    setAttachedFiles([]);
  };

  const deduplicatedMessages = useMemo(() => {
    const result: MessageProps[] = [];
    for (const msg of messages) {
      const last = result[result.length - 1];
      if (last && last.role === msg.role && last.text === msg.text) continue;
      result.push(msg);
    }
    return result;
  }, [messages]);

  // Starter card click: fill the input, then wait one frame for the
  // controlled value to render before focusing and placing the caret at the end.
  const handleStarterSelect = (text: string) => {
    setUserInput(text);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(el.value.length, el.value.length);
      }
    });
  };

  return (
    <div className={styles.container}>
      <div className={styles.leftPanel}>
        <button
          type="button"
          onClick={createNewThread}
          className={styles.newChatBtn}
        >
          <SquarePen size={16} />
          New Chat
        </button>
        <ThreadList
          ref={threadListRef}
          currentThreadId={threadId}
          onThreadSelect={handleThreadSelect}
        />
        <button
          type="button"
          onClick={() => setShowJoinModal(true)}
          className={styles.joinTeamBtn}
        >
          <Users size={16} />
          Join Team Chat
        </button>
        <SidebarAccount />
      </div>
    <div className={styles.chatContainer}>
      <div className={styles.messages} ref={messagesContainerRef}>
        {/* isDraftThread: a thread minted by an attach but with no message yet —
            the conversation hasn't started, so keep the welcome screen. */}
        {!threadId || isDraftThread ? (
          <WelcomeMessage
            onPromptSelect={handleStarterSelect}
            onAttachClick={() => fileInputRef.current?.click()}
          />
        ) : (
          <>
            {deduplicatedMessages.map((msg, index) => (
              <Message key={`${msg.role}-${index}`} role={msg.role} text={msg.text} annotations={msg.annotations} />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>
      <form
        onSubmit={handleSubmit}
        className={`${styles.inputForm} ${styles.clearfix}`}
      >
        {showJoinModal && (
          <div className={styles.modal}>
            <div className={styles.modalContent}>
              <h3>Join Team Chat</h3>
              <input
                type="text"
                value={joinThreadInput}
                onChange={(e) => setJoinThreadInput(e.target.value)}
                placeholder="Enter Team Thread ID"
              />
              <div className={styles.modalButtons}>
                <button onClick={() => setShowJoinModal(false)}>Cancel</button>
                <button onClick={handleJoinTeam}>Join</button>
              </div>
            </div>
          </div>
        )}
        {/* Hidden file input — kept in the DOM, triggered by the "+" button.
            Same picker the old "Attach files" button used. */}
        <input
          type="file"
          ref={fileInputRef}
          className="hidden"
          accept={FILE_ACCEPT}
          multiple
          onChange={handleFileAttach}
        />
        <div className={styles.inputContainer}>
          {attachedFiles.length > 0 && (
            <div className={styles.attachmentChips}>
              {attachedFiles.map((file) => (
                <div
                  key={file.id}
                  className={`${styles.chip} ${
                    file.status === "error" ? styles.chipError : ""
                  }`}
                  title={file.status === "error" ? file.error : undefined}
                >
                  {file.status === "uploading" && (
                    <Loader2
                      size={16}
                      className={`${styles.chipIcon} ${styles.spinner}`}
                    />
                  )}
                  {file.status === "ready" && !file.settled && (
                    <Check size={16} color="#4ade80" className={styles.chipIcon} />
                  )}
                  {file.status === "ready" && file.settled && (
                    <FileIcon size={14} className={styles.chipIcon} />
                  )}
                  {file.status === "error" && (
                    <AlertCircle size={16} color="#f87171" className={styles.chipIcon} />
                  )}
                  <span className={styles.chipText}>
                    <span
                      className={styles.chipName}
                      title={file.status === "error" ? file.error : file.name}
                    >
                      {file.name}
                    </span>
                    {file.status === "uploading" && (
                      <span className={styles.chipStage}>
                        {stageLabel(file.stage)}
                      </span>
                    )}
                  </span>
                  <button
                    type="button"
                    className={styles.chipRemove}
                    onClick={() => removeAttachedFile(file.id)}
                    aria-label={`Remove ${file.name}`}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
          {isUploading && (
            <div className={styles.attachmentNotice}>
              Reading your file — you can ask about it as soon as it&apos;s ready.
            </div>
          )}
          <div className={styles.inputRow}>
            <button
              type="button"
              className={styles.plusBtn}
              onClick={() => fileInputRef.current?.click()}
              title={`Attach files (${SUPPORTED_LABEL})`}
              aria-label="Attach files"
            >
              <Plus size={20} />
            </button>
            <textarea
              ref={textareaRef}
              className={styles.input}
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Enter your question (Shift+Enter for new line)"
              rows={1}
              style={{
                resize: 'vertical',
                minHeight: '40px',
                maxHeight: '200px',
                overflow: 'auto',
              }}
            />
            <button
              type="submit"
              className={styles.button}
              disabled={inputDisabled || isUploading}
              title={isUploading ? "Waiting for files to finish uploading..." : undefined}
            >
              Send
            </button>
          </div>
        </div>
      </form>
    </div>
    </div>
  );
};

export default Chat;
