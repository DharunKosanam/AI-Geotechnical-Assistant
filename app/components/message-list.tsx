"use client";

/**
 * Conversation view. Extracted verbatim from chat.tsx (message components +
 * the .messages region) so the dark-redesign restyle diffs cleanly against a
 * pure move; all conversation state stays owned by chat.tsx.
 */
import React, { useState, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import styles from "./chat.module.css";

export type MessageProps = {
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

type MessageListProps = {
  containerRef: React.RefObject<HTMLDivElement>;
  endRef: React.RefObject<HTMLDivElement>;
  showWelcome: boolean;
  welcome: React.ReactNode;
  messages: MessageProps[];
  showThinking: boolean;
  awaitingSince: number | null;
};

const MessageList = ({
  containerRef,
  endRef,
  showWelcome,
  welcome,
  messages,
  showThinking,
  awaitingSince,
}: MessageListProps) => {
  return (
    <div className={styles.messages} ref={containerRef}>
      {showWelcome ? (
        welcome
      ) : (
        <>
          {messages.map((msg, index) => (
            <Message key={`${msg.role}-${index}`} role={msg.role} text={msg.text} annotations={msg.annotations} />
          ))}
          {showThinking && <ThinkingIndicator startedAt={awaitingSince!} />}
          <div ref={endRef} />
        </>
      )}
    </div>
  );
};

export default MessageList;
