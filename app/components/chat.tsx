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

// --- File attachment config ---
// Text-bearing formats only. Images (PNG/JPG/TIFF) are deliberately NOT offered:
// their text can only be read by OCR, and this deployment has no tesseract
// binary, so every image attachment failed after the fact. The backend rejects
// them up front with the same reasoning (files.py::_validate_upload), and both
// sides key off OCR availability — restore them here if tesseract is installed.
const SUPPORTED_EXTENSIONS = [
  ".pdf", ".docx",
  ".xlsx", ".xls", ".csv",
  ".pptx",
];
// accept attribute for the hidden file input (per UI spec)
const FILE_ACCEPT = ".pdf,.docx,.xlsx,.xls,.csv,.pptx";
const SUPPORTED_LABEL = "PDF, DOCX, XLSX, XLS, CSV, PPTX";
const MAX_UPLOAD_MB = 50;
const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024; // must match backend

// Client-side deadline for a chat response. Above the whole server timeout
// chain (Ollama 180s < /api/chat route 240s < nginx 300s) so it only ever fires
// when the connection itself died without an error — see sendMessage.
const CLIENT_RESPONSE_TIMEOUT_MS = 315_000;

// Streaming watchdog: the backend sends a keep-alive comment every 15s, so this
// much silence means the connection is gone — not that the answer is slow.
const STREAM_SILENCE_TIMEOUT_MS = 60_000;

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

// Last-resort message when an upload fails WITHOUT a usable `detail` from the
// backend — i.e. something between the browser and FastAPI broke. Each case
// names what the user can actually do; "Upload failed (500)" told them nothing.
const httpUploadError = (status: number, filename: string): string => {
  if (status === 401) return "Your session expired. Please sign in again and retry.";
  if (status === 413)
    return `${filename} is too large. The limit is ${MAX_UPLOAD_MB} MB.`;
  if (status === 429)
    return "Too many uploads in a short time. Wait a minute and try again.";
  if (status === 503) return "The server is busy processing uploads. Try again shortly.";
  if (status === 504)
    return "The upload timed out on the way to the server. Check your connection and retry.";
  if (status >= 500)
    return `The server could not accept ${filename} (error ${status}). Please retry; if it keeps failing, report this file.`;
  return `Upload was rejected (error ${status}).`;
};

// Renders the trailing "**Sources:**" markdown block appended to an assistant
// answer. Shared by the streaming and JSON paths so a message looks identical
// however it arrived — sources are always appended at the END, because the
// backend can only finalise the list once the answer is complete (the refusal
// guard can clear it and citation narrowing trims it to what was actually cited).
const formatSourcesBlock = (sources: any[]): string => {
  if (!sources || sources.length === 0) return "";
  let block = "\n\n**Sources:**\n";
  sources.forEach((source: any, index: number) => {
    if (typeof source === "object" && source !== null && source.title && source.url) {
      block += `${index + 1}. [${source.title}](${source.url})\n`;
    } else if (typeof source === "object" && source !== null && source.title) {
      // No URL by design: thread/user uploads are the user's own files, so
      // their citations are plain references — no external link to leak the
      // title to.
      block += `${index + 1}. ${source.title}\n`;
    } else if (typeof source === "string") {
      try {
        const parsed = JSON.parse(source);
        if (parsed.title && parsed.url) {
          block += `${index + 1}. [${parsed.title}](${parsed.url})\n`;
        } else {
          block += `${index + 1}. ${source}\n`;
        }
      } catch {
        block += `${index + 1}. ${source}\n`;
      }
    } else {
      block += `${index + 1}. ${String(source)}\n`;
    }
  });
  return block;
};

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
  // Indexed, but only partially readable (e.g. scanned figure pages were
  // skipped). Not a failure — shown alongside the success state so nobody
  // assumes the assistant can see the whole document.
  warning?: string;
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

// How long to wait before the indicator starts reassuring the user. Under GPU
// contention an answer can take minutes; silence that long reads as "broken"
// and users reload, which adds load and makes the next answer slower still.
const THINKING_SLOW_MS = 15_000;
const THINKING_VERY_SLOW_MS = 45_000;

/**
 * Pending-response indicator — the assistant's turn made visible before any of
 * its text exists. Deliberately mirrors AssistantMessage's row/label/bubble so
 * it occupies exactly the spot the answer will appear in, and is simply
 * replaced by it. Once streaming lands this is the "waiting for first token"
 * state; the streamed text takes over from here.
 */
const ThinkingIndicator = ({ startedAt }: { startedAt: number }) => {
  // Escalating reassurance, driven by two timers rather than a per-second tick
  // so a long wait costs two re-renders, not 180.
  const [phase, setPhase] = useState<0 | 1 | 2>(0);

  useEffect(() => {
    setPhase(0);
    const elapsed = Date.now() - startedAt;
    const timers = [
      setTimeout(() => setPhase(1), Math.max(0, THINKING_SLOW_MS - elapsed)),
      setTimeout(() => setPhase(2), Math.max(0, THINKING_VERY_SLOW_MS - elapsed)),
    ];
    return () => timers.forEach(clearTimeout);
  }, [startedAt]);

  const hint =
    phase === 2
      ? "Still working — complex questions can take a couple of minutes. No need to reload."
      : phase === 1
        ? "Searching the knowledge base and drafting an answer..."
        : "Thinking...";

  return (
    <div className={styles.messageRow} style={{ justifyContent: "flex-start" }}>
      <div className={styles.messageContent}>
        <div className={styles.messageLabel}>AI Assistant</div>
        {/* role=status + aria-live announces the wait to screen readers, which
            would otherwise get the same silence as a blank screen. */}
        <div
          className={`${styles.assistantMessage} ${styles.thinkingBubble}`}
          role="status"
          aria-live="polite"
        >
          <span className={styles.thinkingDots} aria-hidden="true">
            <span className={styles.thinkingDot} />
            <span className={styles.thinkingDot} />
            <span className={styles.thinkingDot} />
          </span>
          <span className={styles.thinkingHint}>{hint}</span>
        </div>
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
  // Answer pending: set the moment the user sends, cleared when the answer OR
  // an error arrives. Kept separate from inputDisabled, which is also toggled
  // by the legacy tool-call flow and by thread switching, so the indicator
  // tracks exactly one thing: "we are waiting on this turn's response".
  const [awaitingSince, setAwaitingSince] = useState<number | null>(null);
  // True from the first byte of an SSE turn until the stream closes. Used to
  // silence the history poll, which would otherwise overwrite the message being
  // streamed with the server's not-yet-written version of it.
  const [isStreaming, setIsStreaming] = useState(false);
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
          // Shared renderer: handles link-less (private upload) citations too.
          text += formatSourcesBlock(sources);
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

  // Streaming turn. Returns false when the backend has streaming switched off
  // (its /chat/stream 404s) so the caller can fall back to the JSON endpoint —
  // the frontend therefore needs no flag of its own and follows STREAMING_ENABLED
  // automatically, with no rebuild to toggle it.
  //
  // Once the first token lands, this NEVER falls back: the turn has already run
  // on the server, so retrying it would generate (and bill, and queue) a second
  // answer for the same question.
  const sendMessageStreaming = async (
    text: string,
    actualThreadId: string | null,
  ): Promise<boolean> => {
    const controller = new AbortController();
    // Watchdog on SILENCE, not on total duration — the backend heartbeats every
    // 15s, so a gap this long means the connection is dead, however long the
    // answer legitimately takes.
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    const resetWatchdog = () => {
      if (watchdog) clearTimeout(watchdog);
      watchdog = setTimeout(() => controller.abort(), STREAM_SILENCE_TIMEOUT_MS);
    };

    let started = false; // has any token been rendered?
    try {
      resetWatchdog();
      const response = await fetch(API_ENDPOINTS.sendMessageStream(), {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(getMessageRequestBody(text, actualThreadId)),
        signal: controller.signal,
      });

      if (response.status === 404) {
        // Streaming disabled server-side — the caller uses /api/chat instead.
        console.log("ℹ️ Streaming disabled on the backend; using /api/chat");
        return false;
      }
      if (!response.ok || !response.body) {
        const raw = await response.text().catch(() => "");
        let detail = "";
        try {
          detail = (JSON.parse(raw) as { detail?: string }).detail ?? "";
        } catch {
          /* not JSON */
        }
        appendMessage(
          "assistant",
          `\n\n[Error: ${detail || `Failed to send message (Status: ${response.status})`}]`,
        );
        setInputDisabled(false);
        return true; // handled — do NOT re-ask via the JSON path
      }

      setIsStreaming(true);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventName = "";

      const handleEvent = (name: string, dataLine: string) => {
        let payload: any;
        try {
          payload = JSON.parse(dataLine);
        } catch {
          return; // malformed frame — ignore rather than break the answer
        }
        if (name === "token") {
          const piece: string = payload.text ?? "";
          if (!piece) return;
          if (!started) {
            started = true;
            shouldForceScrollRef.current = true;
            // First token replaces the thinking indicator: creating the
            // assistant message makes showThinking false on the next render.
            appendMessage("assistant", piece);
          } else {
            appendToLastMessage(piece);
          }
        } else if (name === "done") {
          const block = formatSourcesBlock(payload.sources || []);
          if (block) {
            if (started) appendToLastMessage(block);
            else appendMessage("assistant", block.trimStart());
          }
        } else if (name === "error") {
          const detail = payload.detail || "The assistant failed to answer.";
          if (started) appendToLastMessage(`\n\n[Error: ${detail}]`);
          else appendMessage("assistant", `\n\n[Error: ${detail}]`);
        }
      };

      // SSE framing: records separated by a blank line; within a record,
      // "event:" names it and "data:" carries the JSON. Lines starting with
      // ":" are keep-alive comments and are ignored by design.
      while (true) {
        const { done, value } = await reader.read();
        resetWatchdog();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const record = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          eventName = "";
          for (const line of record.split("\n")) {
            if (line.startsWith("event: ")) eventName = line.slice(7).trim();
            else if (line.startsWith("data: ")) handleEvent(eventName, line.slice(6));
          }
        }
      }

      if (!started) {
        // Stream ended without a single token — treat as a failed turn rather
        // than leaving an empty bubble.
        appendMessage("assistant", "\n\n[Error: The assistant returned an empty response.]");
      }
      setInputDisabled(false);
      return true;
    } catch (error: any) {
      const aborted = error?.name === "AbortError";
      console.error("Streaming error:", error);
      const detail = aborted
        ? "The connection went quiet and the answer was cut off. Please try again."
        : error?.message || "The response stream failed.";
      if (started) appendToLastMessage(`\n\n[Error: ${detail}]`);
      else appendMessage("assistant", `\n\n[Error: ${detail}]`);
      setInputDisabled(false);
      return true; // a partially-streamed turn must never be re-sent
    } finally {
      if (watchdog) clearTimeout(watchdog);
      setIsStreaming(false);
    }
  };

  const sendMessage = async (text: string, targetThreadId: string | null = null) => {
    const actualThreadId = targetThreadId || threadId;

    // Prefer the stream; fall back to the JSON endpoint only when the backend
    // says streaming is off (404), never after tokens have started arriving.
    if (await sendMessageStreaming(text, actualThreadId)) return;

    try {
      // Get API endpoint based on configuration (Python or Next.js)
      const endpoint = API_ENDPOINTS.sendMessage(actualThreadId);
      const requestBody = getMessageRequestBody(text, actualThreadId);
      
      console.log("📤 Sending message to:", endpoint);
      console.log("📦 Request body:", JSON.stringify(requestBody, null, 2));
      console.log("🆔 Thread ID:", actualThreadId);
      
      // Last-resort client deadline. The server chain already fails inside-out
      // (Ollama 180s -> /api/chat route 240s -> nginx 300s), but a connection
      // that silently dies mid-flight — VPN drop, laptop sleep — leaves fetch
      // hanging with no response and no error, and the indicator with nothing
      // to stop it. Sits ABOVE the whole server chain so it never preempts a
      // legitimately slow answer.
      const controller = new AbortController();
      const clientDeadline = setTimeout(() => controller.abort(), CLIENT_RESPONSE_TIMEOUT_MS);

      let response: Response;
      try {
        response = await fetch(endpoint, {
          credentials: "include",
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(requestBody),
          signal: controller.signal,
        });
      } finally {
        clearTimeout(clientDeadline);
      }

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
      const fullResponse = answer + formatSourcesBlock(sources);

      console.log("✅ Answer extracted:", answer.substring(0, 100) + "...");
      console.log("📚 Sources:", sources);

      appendMessage("assistant", fullResponse);
      setInputDisabled(false);

      // Title generation is owned by handleSubmit (the only caller), which knows
      // whether this is the thread's first turn. Doing it here too fired the
      // title LLM twice for a thread started from "New Chat".
    } catch (error: any) {
      console.error("Error sending message:", error);
      const message =
        error?.name === "AbortError"
          ? "The connection was lost before the assistant replied. Please check your connection and try again."
          : error?.message || "Failed to send message";
      appendMessage("assistant", `\n\n[Error: ${message}]`);
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
        if (inputDisabled || !userInput.trim()) {
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
    if (!userInput.trim() || inputDisabled) return;

    const messageText = userInput;
    setUserInput("");
    setInputDisabled(true);
    // Show the indicator NOW — before thread creation, before the request —
    // so there is never a frame in which the app looks idle after a send.
    setAwaitingSince(Date.now());

    // No-op when the thread already exists — whether the user typed into an
    // open conversation or an attach already minted the thread.
    let activeThreadId: string;
    try {
      activeThreadId = await ensureThread();
    } catch (error) {
      console.error("Failed to create thread:", error);
      setUserInput(messageText); // give the text back rather than losing it
      setInputDisabled(false);
      setAwaitingSince(null); // never leave the indicator spinning
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

    try {
      await sendMessage(messageText, activeThreadId);
    } finally {
      // sendMessage handles its own errors today, but a finally is what
      // GUARANTEES the indicator stops — success, HTTP error, timeout, or an
      // exception a future change lets escape. It must never outlive the turn.
      setAwaitingSince(null);
    }

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
          // Shared renderer: handles link-less (private upload) citations too.
          text += formatSourcesBlock(sources);
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
    // This prevents constant polling when idle.
    // NOT while streaming: loadThread() replaces the whole message list from
    // the DB, and the assistant row is not written until the turn completes —
    // so a poll landing mid-stream would wipe the text as it is being typed.
    if (threadId && !isNewThread && inputDisabled && !isStreaming) {
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
  }, [threadId, isNewThread, inputDisabled, isStreaming]);
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
    setAwaitingSince(null); // an abandoned turn must not keep a spinner alive
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
    // The indicator belongs to the thread that was waiting, not to the one
    // being opened — drop it on the switch.
    setAwaitingSince(null);
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
        updateAttachedFile(id, {
          status: "error",
          error: `${filename} is still processing after ${Math.round(
            POLL_TIMEOUT_MS / 60000,
          )} minutes. It may be very large — try a smaller file, or reopen this chat shortly to see if it finished.`,
        });
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
          updateAttachedFile(id, {
            status: "ready",
            stage: undefined,
            warning: data.warning,
          });
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
        // The body is not guaranteed to be JSON: a proxy or gateway failure
        // returns plain text ("Internal Server Error"), and blindly doing
        // resp.json() there is what used to leave the chip reading
        // "Unknown error" with nothing to act on. Read it as text first.
        const raw = await resp.text().catch(() => "");
        let detail = "";
        try {
          detail = (JSON.parse(raw) as { detail?: string }).detail ?? "";
        } catch {
          /* not JSON — fall through to the status-based message */
        }
        console.error(`Failed to upload ${file.name}: ${resp.status}`, raw.slice(0, 500));
        updateAttachedFile(id, {
          status: "error",
          error: detail || httpUploadError(resp.status, file.name),
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
      // fetch only rejects when the request never completed — the connection
      // dropped, or the browser blocked it. Say that, rather than surfacing a
      // raw "Failed to fetch" / "NetworkError" to the user.
      console.error(`Error uploading ${file.name}:`, error);
      updateAttachedFile(id, {
        status: "error",
        error: `Couldn't reach the server to upload ${file.name}. Check your connection (and VPN) and try again.`,
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

  // Waiting, and the answer is not already on screen. The 2s history poll that
  // runs during a wait can surface the assistant's reply slightly before the
  // request resolves; without this the indicator would sit spinning underneath
  // an answer that had already arrived. Phrased as "last message isn't the
  // assistant's" rather than "is the user's" so it also covers the moment
  // before the user's own message has been appended.
  const showThinking =
    awaitingSince !== null &&
    deduplicatedMessages[deduplicatedMessages.length - 1]?.role !== "assistant";

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
            {showThinking && <ThinkingIndicator startedAt={awaitingSince!} />}
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
                  } ${
                    (file.status === "error" && file.error) || file.warning
                      ? styles.chipWithMessage
                      : ""
                  }`}
                  title={file.status === "error" ? file.error : file.warning}
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
                    {/* Say what went wrong IN the chip. A tooltip alone left a
                        failed upload looking like a red filename with no reason. */}
                    {file.status === "error" && file.error && (
                      <span className={styles.chipErrorText}>{file.error}</span>
                    )}
                    {/* Indexed, but part of the document was unreadable. */}
                    {file.status === "ready" && file.warning && (
                      <span className={styles.chipWarningText}>{file.warning}</span>
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
              Reading your file — you can keep chatting; until it&apos;s ready, answers will note it hasn&apos;t been searched yet.
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
              disabled={inputDisabled}
              title={isUploading ? "A document is still processing - you can ask now; the answer will say it was not searched yet." : undefined}
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
