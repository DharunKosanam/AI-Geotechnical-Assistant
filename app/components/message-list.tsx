"use client";

/**
 * Conversation view (dark redesign). Turns render as documents, not bubbles:
 * a mono role label, then the content, on a vertical rail — a hairline tick
 * per user turn, an accent dot per assistant turn.
 *
 * The "Grounded in" sources panel is assembled deterministically from the
 * retrieval payload attached to the message — never from model output. Fields
 * the payload doesn't carry (relevance score, per-chunk page numbers, router
 * mode) are omitted, not guessed. Vision-derived citations keep their
 * "not verbatim" disclaimer.
 */
import React, { useState, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { Check, Copy, RotateCcw, ThumbsUp } from "lucide-react";
import s from "./message-list.module.css";
import { useMessageHighlights } from "./highlights/use-message-highlights";
import type { HighlightActions, StoredHighlight, ThreadHighlights } from "./highlights/use-thread-highlights";

export type MessageProps = {
  role: "user" | "assistant" | "code";
  text: string;
  annotations?: any[];
  /* Retrieval payload for this answer, exactly as the backend sent it. */
  sources?: any[];
  /* Server message id. Present only when HIGHLIGHTS_ENABLED and the message
     is persisted (never mid-stream) -- it is what makes a message highlightable. */
  id?: string;
};

/* Highlight layer input for one assistant message (HIGHLIGHTS_ENABLED only). */
type MessageHighlights = { items: StoredHighlight[]; actions: HighlightActions };
const NO_HIGHLIGHTS: StoredHighlight[] = [];
type RehypePluginList = NonNullable<React.ComponentProps<typeof Markdown>["rehypePlugins"]>;

const UserMessage = ({ text }: { text: string }) => {
  return (
    <article className={`${s.turn} ${s.userTurn}`}>
      <header className={s.turnLabel}>You</header>
      <div className={s.userQuestion}>{text}</div>
    </article>
  );
};

// How long to wait before the indicator starts reassuring the user. Under GPU
// contention an answer can take minutes; silence that long reads as "broken"
// and users reload, which adds load and makes the next answer slower still.
const THINKING_SLOW_MS = 15_000;
const THINKING_VERY_SLOW_MS = 45_000;

/**
 * Pending-response indicator — the assistant's turn made visible before any of
 * its text exists. Mirrors the assistant turn's structure (same rail marker,
 * same label) so the streamed answer replaces it without layout shift.
 *
 * The status line is a single generic line by design: the chat stream exposes
 * no retrieving-vs-generating stages, and inventing them would be fiction.
 * The escalation below is real (client-side elapsed time).
 */
export const ThinkingIndicator = ({ startedAt }: { startedAt: number }) => {
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
    <div className={`${s.turn} ${s.assistantTurn}`}>
      <header className={`${s.turnLabel} ${s.assistantLabel}`}>Assistant</header>
      {/* role=status + aria-live announces the wait to screen readers, which
          would otherwise get the same silence as a blank screen. */}
      <div className={s.thinkingLine} role="status" aria-live="polite">
        <span className={s.thinkingDots} aria-hidden="true">
          <span className={s.thinkingDot} />
          <span className={s.thinkingDot} />
          <span className={s.thinkingDot} />
        </span>
        <span className={s.thinkingHint}>{hint}</span>
      </div>
    </div>
  );
};

/* One normalized row for the sources panel. Mirrors the tolerance of the old
   markdown renderer: source entries may be objects, JSON strings, or plain
   strings depending on age and path. */
type NormalizedSource = {
  title: string;
  url?: string;
  meta: string[];
  vision?: { pages: number[] };
};

const normalizeSource = (source: any): NormalizedSource | null => {
  if (typeof source === "string") {
    try {
      const parsed = JSON.parse(source);
      if (parsed && parsed.title) {
        return { title: parsed.title, url: parsed.url || undefined, meta: [] };
      }
    } catch {
      /* plain string source */
    }
    return { title: source, meta: [] };
  }
  if (typeof source === "object" && source !== null && source.title) {
    const meta: string[] = [];
    if (source.project) meta.push(String(source.project));
    if (source.version != null) meta.push(`v${source.version}`);
    if (source.uploader) meta.push(String(source.uploader));
    return {
      title: source.canonicalTitle || source.title,
      // No URL by design for thread/user uploads: they are the user's own
      // (possibly private) files, so no external link to leak the title to.
      url: source.url || undefined,
      meta,
      vision: source.visionDerived
        ? { pages: Array.isArray(source.visionPages) ? source.visionPages : [] }
        : undefined,
    };
  }
  if (source == null) return null;
  return { title: String(source), meta: [] };
};

const SourcesPanel = ({ sources }: { sources: any[] }) => {
  const rows = sources.map(normalizeSource).filter(Boolean) as NormalizedSource[];
  if (rows.length === 0) return null;
  return (
    <section className={s.sourcesPanel} aria-label="Sources this answer is grounded in">
      <div className={s.sourcesHeader}>
        <span>Grounded in</span>
        <span className={s.sourcesCount}>
          {rows.length} {rows.length === 1 ? "source" : "sources"}
        </span>
      </div>
      {rows.map((row, i) => (
        <div className={s.sourceRow} key={i}>
          <span className={s.sourceIndex}>{String(i + 1).padStart(2, "0")}</span>
          <span className={s.sourceTitle}>
            {row.url ? (
              <a href={row.url} target="_blank" rel="noopener noreferrer">
                {row.title}
              </a>
            ) : (
              row.title
            )}
            {row.meta.length > 0 && (
              <span className={s.sourceMeta}> {row.meta.join(" · ")}</span>
            )}
          </span>
          {row.vision && (
            <span className={s.sourceVision}>
              {row.vision.pages.length > 0
                ? `AI vision · p. ${row.vision.pages.join(", ")} — not verbatim`
                : "AI vision description — not verbatim"}
            </span>
          )}
        </div>
      ))}
    </section>
  );
};

type AssistantMessageProps = {
  text: string;
  annotations?: any[];
  sources?: any[];
  isLast: boolean;
  canRetry: boolean;
  onRetry: () => void;
  /* HIGHLIGHTS_ENABLED only: server id + this message's highlights. Both
     absent -> the body renders exactly as before the feature. */
  id?: string;
  highlights?: MessageHighlights;
};

const AssistantMessage = ({
  text,
  annotations,
  sources,
  isLast,
  canRetry,
  onRetry,
  id,
  highlights,
}: AssistantMessageProps) => {
  const [copied, setCopied] = useState(false);

  // Replace legacy citation annotations like 【6:0†source】 with filenames.
  // (Only the OpenAI-assistants path produces these; the SSE path carries no
  // span-level citations, which is why there are no inline chips to render.)
  const replaceCitationsWithFilenames = (text: string, annotations?: any[]) => {
    const citationRegex = /【(\d+):(\d+)†([^】]+)】/g;

    const citationMap = new Map<string, string>();
    if (annotations && Array.isArray(annotations)) {
      annotations.forEach((annotation: any) => {
        if (annotation.type === 'file_citation' && annotation.file_citation) {
          const filename = annotation.file_citation.filename || 'Unknown File';
          citationMap.set(annotation.text, filename);
        }
      });
    }

    return text.replace(citationRegex, (match) => {
      const filename = citationMap.get(match);
      if (filename) {
        return ` _(Source: ${filename})_ `;
      }
      return ` _(Source: Referenced File)_ `;
    });
  };

  const processedText = replaceCitationsWithFilenames(text, annotations);

  // Highlight layer (HIGHLIGHTS_ENABLED): null unless this message is
  // persisted (has an id) and the renderer input IS the stored content --
  // legacy rows whose citations get rewritten above are excluded, since
  // stored offsets must index the stored text. Null leaves the JSX below
  // byte-identical to the pre-feature rendering.
  const hl = useMessageHighlights({
    enabled: !!highlights && !!id && processedText === text,
    messageId: id,
    source: processedText,
    highlights: highlights?.items ?? NO_HIGHLIGHTS,
    actions: highlights?.actions ?? null,
  });
  const rehypePlugins: RehypePluginList = hl ? [rehypeKatex, hl.rehypePlugin as any] : [rehypeKatex];

  const handleCopy = () => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      })
      .catch(() => {});
  };

  return (
    <article className={`${s.turn} ${s.assistantTurn}`}>
      <header className={`${s.turnLabel} ${s.assistantLabel}`}>Assistant</header>
      <div
        className={s.assistantBody}
        ref={hl?.bodyRef}
        onMouseUp={hl?.onMouseUp}
        onClick={hl?.onClick}
      >
        <Markdown
          remarkPlugins={[remarkGfm, remarkMath]}
          rehypePlugins={rehypePlugins}
          components={{
            // Element styling lives in message-list.module.css (scoped under
            // .assistantBody); only behavior overrides remain here.
            table: ({ node, ...props }) => (
              <div className={s.tableWrap}>
                <table {...props} />
              </div>
            ),
            a: ({ node, ...props }) => (
              <a target="_blank" rel="noopener noreferrer" {...props} />
            ),
          }}
        >
          {processedText}
        </Markdown>
      </div>
      {hl?.popover}
      {sources && sources.length > 0 && <SourcesPanel sources={sources} />}
      <div className={s.turnActions}>
        <button type="button" className={s.actionBtn} onClick={handleCopy}>
          {copied ? <Check size={13} strokeWidth={1.5} /> : <Copy size={13} strokeWidth={1.5} />}
          {copied ? "Copied" : "Copy"}
        </button>
        {isLast && (
          <button
            type="button"
            className={s.actionBtn}
            onClick={onRetry}
            disabled={!canRetry}
            title={canRetry ? "Ask this question again" : "Retry is unavailable right now"}
          >
            <RotateCcw size={13} strokeWidth={1.5} />
            Retry
          </button>
        )}
        <span title="Feedback is not available yet">
          <button type="button" className={s.actionBtn} disabled>
            <ThumbsUp size={13} strokeWidth={1.5} />
            Helpful
          </button>
        </span>
      </div>
    </article>
  );
};

const CodeMessage = ({ text }: { text: string }) => {
  return (
    <article className={`${s.turn} ${s.assistantTurn}`}>
      <header className={`${s.turnLabel} ${s.assistantLabel}`}>Code output</header>
      <div className={s.codeBlock}>
        {text.split("\n").map((line, index) => (
          <div key={index} className={s.codeLine}>
            <span className={s.codeLineNo}>{index + 1}</span>
            {line}
          </div>
        ))}
      </div>
    </article>
  );
};

type MessageListProps = {
  containerRef: React.RefObject<HTMLDivElement>;
  endRef: React.RefObject<HTMLDivElement>;
  showWelcome: boolean;
  welcome: React.ReactNode;
  messages: MessageProps[];
  showThinking: boolean;
  awaitingSince: number | null;
  canRetry: boolean;
  onRetry: () => void;
  /* HIGHLIGHTS_ENABLED only; null/undefined = feature off or no open thread. */
  highlights?: ThreadHighlights | null;
};

const MessageList = ({
  containerRef,
  endRef,
  showWelcome,
  welcome,
  messages,
  showThinking,
  awaitingSince,
  canRetry,
  onRetry,
  highlights,
}: MessageListProps) => {
  const lastAssistantIndex = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return i;
    }
    return -1;
  })();

  return (
    <div className={s.messages} ref={containerRef}>
      {showWelcome ? (
        welcome
      ) : (
        <>
          {messages.map((msg, index) => {
            switch (msg.role) {
              case "user":
                return <UserMessage key={`${msg.role}-${index}`} text={msg.text} />;
              case "assistant":
                return (
                  <AssistantMessage
                    key={`${msg.role}-${index}`}
                    text={msg.text}
                    annotations={msg.annotations}
                    sources={msg.sources}
                    isLast={index === lastAssistantIndex && index === messages.length - 1}
                    canRetry={canRetry}
                    onRetry={onRetry}
                    id={msg.id}
                    highlights={
                      highlights && msg.id
                        ? { items: highlights.byMessage.get(msg.id) ?? NO_HIGHLIGHTS, actions: highlights.actions }
                        : undefined
                    }
                  />
                );
              case "code":
                return <CodeMessage key={`${msg.role}-${index}`} text={msg.text} />;
              default:
                return null;
            }
          })}
          {showThinking && <ThinkingIndicator startedAt={awaitingSince!} />}
          <div ref={endRef} />
        </>
      )}
    </div>
  );
};

export default MessageList;
