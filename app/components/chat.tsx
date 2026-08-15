"use client";

import React, { useState, useEffect, useRef, useMemo, useCallback } from "react";
import useSWR from "swr";
import styles from "./chat.module.css";
import { AssistantStream } from "openai/lib/AssistantStream";
import MessageList, { MessageProps } from "./message-list";
// @ts-expect-error - no types for this yet
import { AssistantStreamEvent } from "openai/resources/beta/assistants/assistants";
import { RequiredActionFunctionToolCall } from "openai/resources/beta/threads/runs/runs";
import Sidebar from "./sidebar";
import Composer from "./composer";
import { API_ENDPOINTS, getMessageRequestBody, isPythonBackend } from "../config/api";
import { MoreHorizontal } from "lucide-react";

// --- File attachment config ---
// DEFAULTS: text-bearing formats only. Images (PNG/JPG/TIFF) are deliberately
// NOT offered by default: their text can only be read by OCR, and this
// deployment has no tesseract binary, so every image attachment failed after
// the fact. The backend rejects them up front with the same reasoning
// (files.py::_validate_upload).
//
// The live list comes from GET /api/upload/config (fetched on mount): when the
// backend runs with VISION_EXTRACTION_ENABLED, it adds JPEG/PNG/WebP -- those
// are read by the AI vision model, not OCR. If that fetch fails (or the flag
// is off) these defaults render, which is exactly today's UI.
const DEFAULT_SUPPORTED_EXTENSIONS = [
  ".pdf", ".docx",
  ".xlsx", ".xls", ".csv",
  ".pptx",
];
const DEFAULT_SUPPORTED_LABEL = "PDF, DOCX, XLSX, XLS, CSV, PPTX";
type UploadTypes = { extensions: string[]; label: string };
const DEFAULT_UPLOAD_TYPES: UploadTypes = {
  extensions: DEFAULT_SUPPORTED_EXTENSIONS,
  label: DEFAULT_SUPPORTED_LABEL,
};
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

// Sources are no longer flattened into the message text: each assistant
// message carries the retrieval payload's sources array as structured data,
// and message-list.tsx renders it as the "Grounded in" panel. The panel is
// assembled deterministically from that payload — never from model output —
// and vision-derived citations keep their "not verbatim" disclaimer there.

const getExt = (filename: string): string => {
  const i = filename.lastIndexOf(".");
  return i >= 0 ? filename.slice(i).toLowerCase() : "";
};
const isSupportedFile = (filename: string, extensions: string[]): boolean =>
  extensions.includes(getExt(filename));

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
  // Diagram chips only (DIAGRAM_EDITOR_ENABLED): "diagram" marks the chip so
  // a ready chip can render its stored PNG as a thumbnail; fileId is the
  // parent doc id the PNG is served under. Document chips never set either,
  // so their rendering is untouched.
  sourceType?: "diagram";
  fileId?: string;
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
  /* Sidebar collapse lives in page.tsx so the top bar toggle and the
     sidebar's own toggle stay in sync. Purely presentational. */
  sidebarCollapsed?: boolean;
  onToggleSidebar?: () => void;
};

const Chat = ({
  functionCallHandler = () => Promise.resolve(""),
  sidebarCollapsed,
  onToggleSidebar,
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
  // The auto-timestamp placeholder ensureThread registered the sidebar row
  // with. The first-action title (format click) passes it as the rename's
  // expectedCurrentName so only the placeholder is ever replaced -- a name
  // the user typed mid-generation wins. Set with awaitingFirstMessageRef,
  // cleared wherever that ref is cleared.
  const draftAutoNameRef = useRef<string | null>(null);
  const [isGroupConversation, setIsGroupConversation] = useState(false);
  const threadListRef = useRef<any>(null);
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [joinThreadInput, setJoinThreadInput] = useState('');
  // Sub-header presentation state: the open thread's display name (fed by the
  // thread list on select/rename) and the sub-header's ⋯ menu.
  const [activeThreadTitle, setActiveThreadTitle] = useState<string | null>(null);
  const [showThreadMenu, setShowThreadMenu] = useState(false);
  const [threadIdCopied, setThreadIdCopied] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // --- File attachment UI state (replaces the removed right-hand file panel) ---
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachedFiles, setAttachedFiles] = useState<AttachedFile[]>([]);
  // Live upload-type list from the backend (adds JPEG/PNG/WebP when vision
  // extraction is enabled server-side). Defaults -- today's text-only list --
  // render until the fetch lands, and stay if it fails.
  const [uploadTypes, setUploadTypes] = useState<UploadTypes>(DEFAULT_UPLOAD_TYPES);
  // Diagram editor capability (backend DIAGRAM_EDITOR_ENABLED), from the same
  // handshake. The field is OMITTED by a flag-off server, and absent/false
  // keeps the plain "+" button — fails closed, like the uploadTypes default.
  const [diagramEditorEnabled, setDiagramEditorEnabled] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showDiagramEditor, setShowDiagramEditor] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch(API_ENDPOINTS.uploadConfig(), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        if (Array.isArray(data.extensions) && data.extensions.length > 0 && data.label) {
          setUploadTypes({ extensions: data.extensions, label: data.label });
        }
        if (data.diagramEditor === true) {
          setDiagramEditorEnabled(true);
        }
      })
      .catch(() => {
        /* keep defaults -- identical to today's UI */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  // True while any chip is still uploading/processing — used to block sending.
  const isUploading = attachedFiles.some((f) => f.status === "uploading");

  // --- Source-grounded output formats (backend SOURCE_FORMATS_ENABLED) ---
  // null until the handshake says the feature is on; fails closed like the
  // Header's workspace/KB gating. readyDocs comes from the status endpoint so
  // a revisited thread (whose uploads happened in an earlier session) still
  // shows the buttons; the OR with ready chips covers a just-finished upload
  // without waiting for a refetch.
  const [availableFormats, setAvailableFormats] = useState<
    { key: string; label: string }[] | null
  >(null);
  const [formatReadyDocs, setFormatReadyDocs] = useState(0);
  const [formatBusy, setFormatBusy] = useState(false);
  const [formatProgress, setFormatProgress] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch(API_ENDPOINTS.formatsStatus(), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data?.enabled || !Array.isArray(data.formats)) return;
        setAvailableFormats(data.formats);
      })
      .catch(() => {
        /* feature stays hidden */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const refreshFormatReadyDocs = useCallback(
    (tid: string | null) => {
      if (!availableFormats || !tid) {
        setFormatReadyDocs(0);
        return;
      }
      fetch(API_ENDPOINTS.formatsStatus(tid), { credentials: "include" })
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          if (typeof data?.readyDocuments === "number") {
            setFormatReadyDocs(data.readyDocuments);
          }
        })
        .catch(() => {
          /* keep last known count */
        });
    },
    [availableFormats],
  );
  useEffect(() => {
    refreshFormatReadyDocs(threadId || null);
  }, [threadId, refreshFormatReadyDocs]);
  const hasReadyFormatDocs =
    formatReadyDocs > 0 || attachedFiles.some((f) => f.status === "ready");

  // --- Named persistent source sets (backend SOURCE_SETS_ENABLED) ---
  // The sources panel: per-document status, chunk count, and provenance for
  // the current thread, with per-source removal behind a dry-run confirm.
  // Fails closed like the formats handshake.
  interface ThreadSource {
    filename: string;
    status: string;
    reason: string | null;
    chunkCount: number;
    provenance: "verbatim" | "vision" | "mixed";
    visionPages: number[];
    partiallyIndexed: boolean;
    warning: string | null;
  }
  const [sourceSetsEnabled, setSourceSetsEnabled] = useState(false);
  const [showSources, setShowSources] = useState(false);
  const [threadSources, setThreadSources] = useState<ThreadSource[] | null>(null);
  // The dry-run preview for the source pending removal; confirm sends the
  // same request with confirm: true.
  const [removePreview, setRemovePreview] = useState<{
    filename: string;
    chunksToDelete: number;
    parentDocsToDelete: number;
  } | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  useEffect(() => {
    let cancelled = false;
    fetch(API_ENDPOINTS.sourceSetsStatus(), { credentials: "include" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled && data?.enabled) setSourceSetsEnabled(true);
      })
      .catch(() => {
        /* feature stays hidden */
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const refreshThreadSources = useCallback(
    (tid: string | null) => {
      if (!sourceSetsEnabled || !tid) {
        setThreadSources(null);
        return;
      }
      fetch(API_ENDPOINTS.threadSources(tid), { credentials: "include" })
        .then((res) => (res.ok ? res.json() : null))
        .then((data) => {
          if (Array.isArray(data?.sources)) setThreadSources(data.sources);
        })
        .catch(() => {
          /* keep last known list */
        });
    },
    [sourceSetsEnabled],
  );
  useEffect(() => {
    setShowSources(false);
    setRemovePreview(null);
    refreshThreadSources(threadId || null);
  }, [threadId, refreshThreadSources]);

  // Ask for the dry-run preview; the actual delete happens in confirmRemoveSource.
  const requestRemoveSource = async (filename: string) => {
    const tid = threadIdRef.current;
    if (!tid || removeBusy) return;
    setRemoveBusy(true);
    try {
      const res = await fetch(API_ENDPOINTS.removeThreadSource(tid), {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename, confirm: false }),
      });
      if (res.ok) {
        const data = await res.json();
        setRemovePreview({
          filename,
          chunksToDelete: data.chunksToDelete ?? 0,
          parentDocsToDelete: data.parentDocsToDelete ?? 0,
        });
      }
    } catch (e) {
      console.error("Source removal preview failed:", e);
    } finally {
      setRemoveBusy(false);
    }
  };

  const confirmRemoveSource = async () => {
    const tid = threadIdRef.current;
    if (!tid || !removePreview || removeBusy) return;
    setRemoveBusy(true);
    try {
      const res = await fetch(API_ENDPOINTS.removeThreadSource(tid), {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: removePreview.filename, confirm: true }),
      });
      if (res.ok) {
        // The document set changed: refresh the panel, the format-button
        // gating, and drop a matching attachment chip if one is showing.
        setAttachedFiles((prev) => prev.filter((f) => f.name !== removePreview.filename));
        refreshThreadSources(tid);
        refreshFormatReadyDocs(tid);
      }
    } catch (e) {
      console.error("Source removal failed:", e);
    } finally {
      setRemovePreview(null);
      setRemoveBusy(false);
    }
  };
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
        const text = msg.content?.[0]?.text?.value || msg.content || '';
        const sources = msg.sources || [];
        return {
          role: msg.role,
          text,
          annotations: msg.content?.[0]?.text?.annotations || [],
          // Legacy rows may already carry the old "**Sources:**" block inside
          // the text — attach the structured panel only when they don't, so
          // sources never render twice.
          sources:
            msg.role === 'assistant' && sources.length > 0 && !text.includes('**Sources:**')
              ? sources
              : undefined,
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

      // Same first-action rename semantics as applyFormatTitle: the title
      // only ever replaces the auto-timestamp placeholder captured when the
      // thread was minted. No placeholder on record means nothing is safely
      // replaceable, so skip the LLM call too.
      const placeholder = draftAutoNameRef.current;
      if (!placeholder) return;

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

      // Update thread name in history -- conditionally: expectedCurrentName
      // pins the write to the placeholder, so a rename the user made while
      // the answer was streaming wins (the backend answers 409 and whatever
      // won stays).
      const updateEndpoint = API_ENDPOINTS.updateThread();
      const update = await fetch(updateEndpoint, {
        credentials: "include",
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          threadId: targetThreadId,
          newName: title,
          expectedCurrentName: placeholder,
        }),
      });
      if (!update.ok) return; // 409: someone named the thread first

      // Refresh thread list to show updated title
      if (threadListRef.current) {
        await threadListRef.current.fetchThreads();
      }
    } catch (error) {
      console.error("Error generating title:", error);
    }
  };

  // First-action title for a thread whose opening act is a generated
  // document. Deterministic "<Format label> - <source title>" instead of the
  // LLM summarizer: it names the artifact rather than summarizing its text,
  // costs no model call right after a 60-90s generation on the same GPU, and
  // is stable across retries. The rename is conditional: expectedCurrentName
  // pins it to the auto-timestamp placeholder, so a name the user typed
  // mid-generation -- or any title that landed first -- survives (the
  // backend answers 409 and whatever won stays).
  const applyFormatTitle = async (
    targetThreadId: string,
    formatLabel: string,
    sources: any[],
  ) => {
    const placeholder = draftAutoNameRef.current;
    if (!placeholder) return; // nothing safely replaceable
    const first = sources.find(
      (s) => s && typeof s === "object" && typeof s.title === "string" && s.title,
    );
    let title = formatLabel;
    if (first) {
      const extra = sources.length > 1 ? ` and ${sources.length - 1} more` : "";
      const room = 80 - formatLabel.length - 3 - extra.length;
      let doc: string = first.title;
      if (doc.length > room) doc = doc.slice(0, Math.max(10, room - 3)) + "...";
      title = `${formatLabel} - ${doc}${extra}`;
    }
    try {
      const res = await fetch(API_ENDPOINTS.updateThread(), {
        credentials: "include",
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId: targetThreadId,
          newName: title,
          expectedCurrentName: placeholder,
        }),
      });
      // 409: someone named the thread first (user rename, chat title from
      // another tab). Theirs stands; nothing to do.
      if (res.ok && threadListRef.current) {
        await threadListRef.current.fetchThreads();
      }
    } catch (error) {
      console.error("First-action title update failed:", error);
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
          const sources = payload.sources || [];
          if (sources.length > 0) {
            if (started) attachSourcesToLastMessage(sources);
            else appendMessage("assistant", "", sources);
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

  // Generate a source-grounded format document (study guide, briefing, ...)
  // over the formats SSE endpoint. Mirrors sendMessageStreaming's transport
  // discipline (silence watchdog, never re-send after first token) plus two
  // format-specific events: "progress" (map-reduce batch counts, shown in the
  // notice line until the first token arrives) and the coverage-carrying
  // "done". A 409 means another generation holds the single per-instance job
  // slot — surfaced as a notice, never queued silently.
  const generateFormatDocument = async (formatKey: string, formatLabel: string) => {
    const actualThreadId = threadIdRef.current;
    if (!actualThreadId || formatBusy) return;

    // Is this the thread's first action? Captured before any state changes.
    // The slot is consumed only when a document actually completes (below),
    // so a failed or empty run leaves the naming to the next success --
    // whether that is a retried format click or a first chat message.
    const isFirstAction = awaitingFirstMessageRef.current;

    // Generating a document starts the conversation just like sending a
    // message does (handleSubmit clears this flag the same way). Without
    // this, an attach-first thread is still a draft, the render ternary
    // keeps showing the welcome pane, and the streamed document appends
    // into message state that is never mounted -- received but invisible.
    setIsDraftThread(false);
    setFormatBusy(true);
    setInputDisabled(true);
    setAwaitingSince(Date.now());
    setFormatProgress(
      `Starting ${formatLabel.toLowerCase()} generation — a large document can take a few minutes...`,
    );

    const controller = new AbortController();
    let watchdog: ReturnType<typeof setTimeout> | null = null;
    const resetWatchdog = () => {
      if (watchdog) clearTimeout(watchdog);
      watchdog = setTimeout(() => controller.abort(), STREAM_SILENCE_TIMEOUT_MS);
    };

    let started = false;
    // Sources from the done event, kept for the first-action title below.
    let doneSources: any[] | null = null;
    try {
      resetWatchdog();
      const response = await fetch(API_ENDPOINTS.formatsStream(), {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId: actualThreadId, format: formatKey }),
        signal: controller.signal,
      });

      if (response.status === 409) {
        // Single-job guard: visible refusal, not a silent queue.
        const raw = await response.text().catch(() => "");
        let detail = "A document generation is already running. Please try again in a moment.";
        try {
          detail = (JSON.parse(raw) as { detail?: string }).detail ?? detail;
        } catch {
          /* keep default */
        }
        setFormatProgress(detail);
        setTimeout(() => setFormatProgress(null), 6000);
        return;
      }
      if (response.status === 404) {
        // Feature switched off server-side since the handshake: hide the UI.
        setAvailableFormats(null);
        return;
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
          `\n\n[Error: ${detail || `Failed to generate document (Status: ${response.status})`}]`,
        );
        return;
      }

      setIsStreaming(true);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventName = "";

      const stageLabels: Record<string, string> = {
        prepare: "Preparing",
        load: "Loading the model",
        read: "Reading the document",
        write: "Writing the document",
        map: "Summarizing sections",
        reduce: "Synthesizing document",
        generate: "Generating document",
      };

      const handleEvent = (name: string, dataLine: string) => {
        let payload: any;
        try {
          payload = JSON.parse(dataLine);
        } catch {
          return;
        }
        if (name === "token") {
          const piece: string = payload.text ?? "";
          if (!piece) return;
          if (!started) {
            started = true;
            shouldForceScrollRef.current = true;
            setFormatProgress(null);
            appendMessage("assistant", piece);
          } else {
            appendToLastMessage(piece);
          }
        } else if (name === "progress") {
          const label = stageLabels[payload.stage] ?? "Working";
          if (payload.stage === "load" && payload.total > 0) {
            // Cold start (first use after a reboot / Ollama restart): the
            // model weights load from disk before anything else can happen.
            setFormatProgress(
              `Loading the model — first run after a restart, about ${payload.total}s...`,
            );
          } else if (payload.stage === "read" && payload.total > 0) {
            // Prefill wait: show the honest estimate and a moving elapsed
            // counter so a multi-minute silence reads as work, not a hang.
            const elapsed = payload.current > 0 ? ` — ${payload.current}s elapsed` : "";
            setFormatProgress(
              `Reading the document (about ${payload.total}s for this document)${elapsed}...`,
            );
          } else {
            const counted =
              typeof payload.current === "number" && payload.total > 1
                ? ` (${payload.current}/${payload.total})`
                : "";
            setFormatProgress(`${label}${counted}...`);
          }
        } else if (name === "done") {
          doneSources = payload.sources || [];
          if (doneSources.length > 0) {
            if (started) attachSourcesToLastMessage(doneSources);
            else appendMessage("assistant", "", doneSources);
          }
        } else if (name === "error") {
          const detail = payload.detail || "The document generation failed.";
          if (started) appendToLastMessage(`\n\n[Error: ${detail}]`);
          else appendMessage("assistant", `\n\n[Error: ${detail}]`);
        }
      };

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
        appendMessage(
          "assistant",
          "\n\n[Error: The document generation returned no content.]",
        );
      } else if (isFirstAction) {
        // First action names the thread -- once. The conditional rename in
        // applyFormatTitle only ever replaces the auto-timestamp
        // placeholder, so a user rename that landed mid-generation wins.
        awaitingFirstMessageRef.current = false;
        await applyFormatTitle(actualThreadId, formatLabel, doneSources ?? []);
      }
    } catch (error: any) {
      const aborted = error?.name === "AbortError";
      console.error("Format generation error:", error);
      const detail = aborted
        ? "The connection went quiet and the generation was cut off. Please try again."
        : error?.message || "The generation stream failed.";
      if (started) appendToLastMessage(`\n\n[Error: ${detail}]`);
      else appendMessage("assistant", `\n\n[Error: ${detail}]`);
    } finally {
      if (watchdog) clearTimeout(watchdog);
      setIsStreaming(false);
      setFormatBusy(false);
      setInputDisabled(false);
      setAwaitingSince(null);
      setFormatProgress((p) =>
        p && p.startsWith("A document generation is already running") ? p : null,
      );
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

      console.log("✅ Answer extracted:", answer.substring(0, 100) + "...");
      console.log("📚 Sources:", sources);

      appendMessage("assistant", answer, sources);
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
      const placeholderName = getDefaultThreadName();
      draftAutoNameRef.current = placeholderName;
      await fetch(API_ENDPOINTS.createThreadHistory(), {
        credentials: "include",
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          threadId: newThreadId,
          name: placeholderName,
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

  // Retry: re-ask the last user question through the exact same send path a
  // typed message takes. The repeated user turn is hidden by the consecutive-
  // duplicate dedup, so on screen this reads as "regenerate the answer".
  const retryLastTurn = async () => {
    if (inputDisabled || isStreaming) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    const activeThreadId = threadIdRef.current;
    if (!lastUser || !activeThreadId) return;
    setInputDisabled(true);
    setAwaitingSince(Date.now());
    shouldForceScrollRef.current = true;
    setMessages((prevMessages) => {
      const newMessages: MessageProps[] = [
        ...prevMessages,
        { role: "user" as const, text: lastUser.text },
      ];
      lastMessageCountRef.current = newMessages.length;
      return newMessages;
    });
    try {
      await sendMessage(lastUser.text, activeThreadId);
    } finally {
      setAwaitingSince(null);
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

  const appendMessage = (role: "user" | "assistant" | "code", text: string, sources?: any[]) => {
    setMessages((prevMessages) => {
      const newMessages = [...prevMessages, { role, text, sources }];
      lastMessageCountRef.current = newMessages.length;
      return newMessages;
    });
  };

  // Attach the retrieval payload's sources to the message the panel belongs
  // to (the streamed answer that just finished).
  const attachSourcesToLastMessage = (sources: any[]) => {
    if (!sources || sources.length === 0) return;
    setMessages((prevMessages) => {
      if (prevMessages.length === 0) return prevMessages;
      const last = prevMessages[prevMessages.length - 1];
      return [...prevMessages.slice(0, -1), { ...last, sources }];
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
        const text = msg.content?.[0]?.text?.value || msg.content || '';
        const sources = msg.sources || [];
        return {
          role: msg.role,
          text,
          annotations: msg.content?.[0]?.text?.annotations || [],
          // Legacy rows may already carry the old "**Sources:**" block inside
          // the text — attach the structured panel only when they don't, so
          // sources never render twice.
          sources:
            msg.role === 'assistant' && sources.length > 0 && !text.includes('**Sources:**')
              ? sources
              : undefined,
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
    setActiveThreadTitle(null);
    setIsNewThread(true);
    setIsDraftThread(false);
    setAwaitingSince(null); // an abandoned turn must not keep a spinner alive
    awaitingFirstMessageRef.current = false;
    draftAutoNameRef.current = null;
    clearAttachedFiles();
    setUserInput("");
    lastMessageCountRef.current = 0;
  };

  const handleThreadSelect = (selectedThreadId: string | null, isGroup: boolean, name?: string) => {
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
    draftAutoNameRef.current = null;

    if (selectedThreadId === null) {
      // Show welcome screen - clear the current thread
      setThreadId("");
      threadIdRef.current = "";
      setMessages([]);
      setIsGroupConversation(false);
      setActiveThreadTitle(null);
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
      setActiveThreadTitle(name ?? null);
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

  // Escape closes the join modal and the sub-header ⋯ menu.
  useEffect(() => {
    if (!showJoinModal && !showThreadMenu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setShowJoinModal(false);
        setShowThreadMenu(false);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [showJoinModal, showThreadMenu]);

  const copyThreadId = () => {
    if (!threadId) return;
    navigator.clipboard?.writeText(threadId).catch(() => {});
    setThreadIdCopied(true);
    window.setTimeout(() => {
      setThreadIdCopied(false);
      setShowThreadMenu(false);
    }, 1200);
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
          // A newly-ready document can enable the format buttons and
          // belongs in the sources panel.
          refreshFormatReadyDocs(forThreadId);
          refreshThreadSources(forThreadId);
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

  // --- Diagram upload (DIAGRAM_EDITOR_ENABLED) ---

  // Filename for a drawn diagram, derived from the draw.io page title: slug of
  // [a-z0-9-], max 40 chars, fallback "diagram" for an empty/Untitled title,
  // then a 6-char random suffix + ".png". Chips, status polling, retrieval and
  // the scope note all key on filename, so two "Untitled" diagrams in one
  // thread must never collide — the suffix guarantees uniqueness.
  const diagramFilename = (xml: string): string => {
    let title = "";
    try {
      const doc = new DOMParser().parseFromString(xml, "text/xml");
      title = doc.querySelector("diagram")?.getAttribute("name") ?? "";
    } catch {
      /* no title — fall through to the default name */
    }
    let slug = title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40)
      .replace(/-+$/g, "");
    if (!slug || slug === "untitled") slug = "diagram";
    const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
    let suffix = "";
    if (typeof crypto !== "undefined" && crypto.getRandomValues) {
      const bytes = crypto.getRandomValues(new Uint8Array(6));
      bytes.forEach((b) => (suffix += alphabet[b % alphabet.length]));
    } else {
      for (let i = 0; i < 6; i++)
        suffix += alphabet[Math.floor(Math.random() * alphabet.length)];
    }
    return `${slug}-${suffix}.png`;
  };

  // draw.io's xmlpng export arrives as a data URI; the upload endpoint takes
  // multipart, so decode it to a Blob. Null for anything that isn't the
  // base64 PNG form we asked for.
  const pngDataUriToBlob = (dataUri: string): Blob | null => {
    const match = /^data:image\/png;base64,(.+)$/.exec(dataUri);
    if (!match) return null;
    try {
      const bytes = atob(match[1]);
      const arr = new Uint8Array(bytes.length);
      for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
      return new Blob([arr], { type: "image/png" });
    } catch {
      return null;
    }
  };

  // Uploads a drawn diagram INTO the thread: PNG (display) + XML (the indexed
  // source, flattened server-side with zero model calls). Deliberately a
  // SIBLING of uploadAttachedFile rather than a change to it — the document
  // upload path stays untouched — but the chip lifecycle downstream of the
  // POST (startPolling -> ready/failed) is the same one documents use.
  const uploadDiagram = async (pngDataUri: string, xml: string) => {
    const blob = pngDataUriToBlob(pngDataUri);
    if (!blob || !xml.trim()) {
      alert("The editor returned an unusable diagram export. Please try saving again.");
      return;
    }
    const filename = diagramFilename(xml);
    const id =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${filename}`;
    setAttachedFiles((prev) => [
      ...prev,
      { id, name: filename, status: "uploading" as const, sourceType: "diagram" as const },
    ]);

    // Same rule as handleFileAttach: a diagram is always uploaded INTO a
    // thread, so mint one if drawing came before the first message.
    let forThreadId: string;
    try {
      forThreadId = await ensureThread();
    } catch (error) {
      console.error("Failed to create a thread for the diagram:", error);
      updateAttachedFile(id, {
        status: "error",
        error: "Couldn't start a conversation for this diagram. Please try again.",
      });
      return;
    }
    updateAttachedFile(id, { threadId: forThreadId });

    const data = new FormData();
    data.append("file", blob, filename);
    data.append("threadId", forThreadId);
    data.append("sourceType", "diagram");
    data.append("diagramXml", xml);

    try {
      const resp = await fetch(API_ENDPOINTS.uploadFile(), {
        credentials: "include",
        method: "POST",
        body: data,
      });
      if (!resp.ok) {
        // Same text-first error handling as uploadAttachedFile: the body is
        // not guaranteed to be JSON when a proxy or gateway fails.
        const raw = await resp.text().catch(() => "");
        let detail = "";
        try {
          detail = (JSON.parse(raw) as { detail?: string }).detail ?? "";
        } catch {
          /* not JSON — fall through to the status-based message */
        }
        console.error(`Failed to upload ${filename}: ${resp.status}`, raw.slice(0, 500));
        updateAttachedFile(id, {
          status: "error",
          error: detail || httpUploadError(resp.status, filename),
        });
        return;
      }
      // Accepted: ingestion (XML flatten -> chunk -> embed) runs in a backend
      // background task; poll the same status endpoint documents use. The
      // response's file_id is where the stored PNG is served from — kept on
      // the chip so the ready state can render a thumbnail. A body that
      // fails to parse only costs the thumbnail, never the chip lifecycle.
      const result = await resp.json().catch(() => ({} as { file_id?: string }));
      updateAttachedFile(id, { stage: "processing", fileId: result.file_id });
      startPolling(id, filename, forThreadId);
    } catch (error) {
      console.error(`Error uploading ${filename}:`, error);
      updateAttachedFile(id, {
        status: "error",
        error: `Couldn't reach the server to upload ${filename}. Check your connection (and VPN) and try again.`,
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
      if (!isSupportedFile(f.name, uploadTypes.extensions)) {
        alert(`"${f.name}" is not a supported file type.\n\nSupported: ${uploadTypes.label}`);
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
      <Sidebar
        threadListRef={threadListRef}
        currentThreadId={threadId}
        onThreadSelect={handleThreadSelect}
        onNewChat={createNewThread}
        onJoinTeam={() => setShowJoinModal(true)}
        collapsed={sidebarCollapsed}
        onToggleCollapse={onToggleSidebar}
        onThreadRenamed={(renamedId, name) => {
          if (renamedId === threadId) setActiveThreadTitle(name);
        }}
      />
    <div className={styles.chatContainer}>
      {threadId && !isDraftThread && (
        <div className={styles.threadHeader}>
          <div className={styles.threadHeaderMain}>
            <span className={styles.threadTitle}>
              {activeThreadTitle || "Conversation"}
            </span>
            <span className={styles.threadMeta}>
              {deduplicatedMessages.filter((m) => m.role === "user").length}{" "}
              {deduplicatedMessages.filter((m) => m.role === "user").length === 1
                ? "turn"
                : "turns"}
            </span>
          </div>
          <div className={styles.threadHeaderActions}>
            {isGroupConversation ? (
              <span className={styles.labSharedChip}>LAB SHARED</span>
            ) : (
              <span title="Share this thread from its ⋯ menu in the sidebar">
                <button type="button" className={styles.threadHeaderBtn} disabled>
                  Share with lab
                </button>
              </span>
            )}
            <span title="Export is not available yet">
              <button type="button" className={styles.threadHeaderBtn} disabled>
                Export
              </button>
            </span>
            <div className={styles.threadMenuWrap}>
              <button
                type="button"
                className={styles.threadHeaderBtn}
                aria-label="Thread menu"
                aria-haspopup="menu"
                aria-expanded={showThreadMenu}
                onClick={() => setShowThreadMenu((open) => !open)}
              >
                <MoreHorizontal size={14} />
              </button>
              {showThreadMenu && (
                <>
                  <div
                    className={styles.menuBackdrop}
                    onClick={() => setShowThreadMenu(false)}
                  />
                  <div className={styles.threadMenu} role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      className={styles.threadMenuItem}
                      onClick={copyThreadId}
                    >
                      {threadIdCopied ? "Copied" : "Copy thread ID"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
      {/* isDraftThread: a thread minted by an attach but with no message yet —
          the conversation hasn't started, so keep the welcome screen. */}
      <MessageList
        containerRef={messagesContainerRef}
        endRef={messagesEndRef}
        showWelcome={!threadId || isDraftThread}
        welcome={
          <WelcomeMessage
            onPromptSelect={handleStarterSelect}
            onAttachClick={() => fileInputRef.current?.click()}
          />
        }
        messages={deduplicatedMessages}
        showThinking={showThinking}
        awaitingSince={awaitingSince}
        canRetry={!inputDisabled && !isStreaming && !isGroupConversation}
        onRetry={retryLastTurn}
      />
      <Composer
        onSubmit={handleSubmit}
        showJoinModal={showJoinModal}
        setShowJoinModal={setShowJoinModal}
        joinThreadInput={joinThreadInput}
        setJoinThreadInput={setJoinThreadInput}
        handleJoinTeam={handleJoinTeam}
        showDiagramEditor={showDiagramEditor}
        setShowDiagramEditor={setShowDiagramEditor}
        uploadDiagram={uploadDiagram}
        fileInputRef={fileInputRef}
        uploadTypes={uploadTypes}
        handleFileAttach={handleFileAttach}
        attachedFiles={attachedFiles}
        removeAttachedFile={removeAttachedFile}
        isUploading={isUploading}
        sourceSetsEnabled={sourceSetsEnabled}
        threadId={threadId}
        showSources={showSources}
        setShowSources={setShowSources}
        threadSources={threadSources}
        removePreview={removePreview}
        setRemovePreview={setRemovePreview}
        removeBusy={removeBusy}
        formatBusy={formatBusy}
        isStreaming={isStreaming}
        requestRemoveSource={requestRemoveSource}
        confirmRemoveSource={confirmRemoveSource}
        availableFormats={availableFormats}
        hasReadyFormatDocs={hasReadyFormatDocs}
        generateFormatDocument={generateFormatDocument}
        formatProgress={formatProgress}
        diagramEditorEnabled={diagramEditorEnabled}
        showAttachMenu={showAttachMenu}
        setShowAttachMenu={setShowAttachMenu}
        textareaRef={textareaRef}
        userInput={userInput}
        setUserInput={setUserInput}
        handleKeyDown={handleKeyDown}
        inputDisabled={inputDisabled}
      />
    </div>
    </div>
  );
};

export default Chat;
