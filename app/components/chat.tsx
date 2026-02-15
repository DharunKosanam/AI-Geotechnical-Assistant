"use client";

import React, { useState, useEffect, useRef } from "react";
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
import { API_ENDPOINTS, getMessageRequestBody, isPythonBackend } from "../config/api";

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

const WelcomeMessage = () => {
  return (
    <div className={styles.welcomeContainer}>
      <div className={styles.welcomeMessage}>
        <h1>Hello! 👋</h1>
        <p>How can I assist you with your geotechnical reports today?</p>
        <div className={styles.welcomeHints}>
          <p>💡 Start a new conversation or select a previous chat from the sidebar.</p>
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
  const [isGroupConversation, setIsGroupConversation] = useState(false);
  const threadListRef = useRef<any>(null);
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [joinThreadInput, setJoinThreadInput] = useState('');
  
  const [shouldScroll, setShouldScroll] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  // Track if this is a new thread that hasn't had a message sent yet
  const [isNewThread, setIsNewThread] = useState(false);
  // Track polling interval for real-time updates
  const pollingIntervalRef = useRef<NodeJS.Timeout | null>(null);
  // Track last message count to detect new messages
  const lastMessageCountRef = useRef<number>(0);

  // 添加一个生成默认名称的函数
  const getDefaultThreadName = () => {
    return new Date().toLocaleString();
  };

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    if (shouldScroll) {
      // Use setTimeout to ensure DOM has updated
      setTimeout(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
        setShouldScroll(false);
      }, 100);
    }
  }, [shouldScroll]);


  // SWR fetcher function for message history
  const fetcher = async (url: string) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch messages');
    return res.json();
  };

  // SWR polling for real-time message updates (for group conversations)
  const { data: messageData } = useSWR(
    isGroupConversation && threadId ? `/api/assistants/threads/${threadId}/messages-history` : null,
    fetcher,
    {
      refreshInterval: 3000, // Poll every 3 seconds
      revalidateOnFocus: true,
      revalidateOnReconnect: true,
    }
  );

  // Update messages when SWR fetches new data (ONLY for group conversations)
  useEffect(() => {
    if (messageData?.messages && isGroupConversation && threadId) {
      console.log(`[SWR] Group conversation update: ${messageData.messages.length} messages for thread ${threadId}`);
      setMessages(messageData.messages);
      setShouldScroll(true);
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
    try {
      const reader = readableStream.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          console.log("Stream ended");
          setInputDisabled(false);
          break;
        }

        // Decode the chunk and add to buffer
        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE messages (separated by double newlines)
        const messages = buffer.split('\n\n');
        
        // Keep the last incomplete message in the buffer
        buffer = messages.pop() || '';

        for (const message of messages) {
          if (!message.trim()) continue;

          // Parse SSE message
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

          if (!data) continue;

          // Handle [DONE] signal from OpenAI
          if (data === '[DONE]') {
            console.log("Stream completed with [DONE] signal");
            setInputDisabled(false);
            continue;
          }

          try {
            const parsed = JSON.parse(data);
            console.log("📩 SSE Event:", eventType, "Data:", parsed);
            
            // Handle different event types
            if (eventType === 'thread.message.created' || parsed.object === 'thread.message') {
              // Message created
              if (parsed.role === 'assistant') {
                appendMessage("assistant", "");
                setShouldScroll(true);
              }
            } else if (eventType === 'thread.message.delta' || parsed.object === 'thread.message.delta') {
              // Message delta - update the last message
              if (parsed.delta?.content) {
                for (const content of parsed.delta.content) {
                  if (content.type === 'text' && content.text?.value) {
                    appendToLastMessage(content.text.value);
                  }
                }
              }
            } else if (eventType === 'thread.run.completed' || parsed.status === 'completed') {
              // Run completed
              console.log("Run completed");
              setInputDisabled(false);
            } else if (eventType === 'thread.run.failed' || parsed.status === 'failed') {
              // Run failed
              console.error("Run failed:", parsed);
              setInputDisabled(false);
              const errorMsg = parsed.last_error?.message || "The assistant run failed. Please try again.";
              appendMessage("assistant", `\n\n[Error: ${errorMsg}]`);
            } else if (eventType === 'thread.run.requires_action') {
              // Handle required actions (tool calls)
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
              // Error in response
              console.error("Stream error:", parsed.error);
              appendMessage("assistant", `\n\n[Error: ${parsed.error}]`);
              setInputDisabled(false);
            }
          } catch (parseError) {
            console.error("Error parsing SSE data:", parseError, "Data:", data);
          }
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
    const isFirstMessage = isNewThread && messages.length === 0;
    
    try {
      // Get API endpoint based on configuration (Python or Next.js)
      const endpoint = API_ENDPOINTS.sendMessage(actualThreadId);
      const requestBody = getMessageRequestBody(text, actualThreadId);
      
      console.log("📤 Sending message to:", endpoint);
      console.log("📦 Request body:", JSON.stringify(requestBody, null, 2));
      console.log("🆔 Thread ID:", actualThreadId);
      
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });
      
      console.log("📨 Response status:", response.status);

      if (!response.ok) {
        let errorMessage = `Failed to send message (Status: ${response.status})`;
        try {
          const errorData = await response.json();
          errorMessage = errorData.error || errorMessage;
          if (errorData.details) {
            console.error("Error details:", errorData.details);
          }
        } catch {
          const errorText = await response.text();
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
      
      // Build the complete response with sources
      let fullResponse = answer;
      
      if (sources && sources.length > 0) {
        fullResponse += "\n\n**Sources:**\n";
        sources.forEach((source: string, index: number) => {
          fullResponse += `${index + 1}. ${source}\n`;
        });
      }
      
      console.log("✅ Answer extracted:", answer.substring(0, 100) + "...");
      console.log("📚 Sources:", sources);
      
      // Add assistant's response to messages
      appendMessage("assistant", fullResponse);
      setShouldScroll(true);
      setInputDisabled(false);

      // Generate title for first message in new thread
      if (isFirstMessage) {
        setIsNewThread(false);
        generateAndUpdateTitle(text, actualThreadId).catch(error => {
          console.error("Error generating title:", error);
        });
      }
      
    } catch (error) {
      console.error("Error sending message:", error);
      appendMessage("assistant", `\n\n[Error: ${error.message || "Failed to send message"}]`);
      setInputDisabled(false);
    }
  };

  const submitActionResult = async (runId: string, toolCallOutputs: any[]) => {
    try {
      const response = await fetch(
        `/api/assistants/threads/${threadId}/actions`,
        {
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
        
        // Don't submit if already processing or input is empty
        if (inputDisabled || !userInput.trim()) {
          return;
        }
        
        // Trigger form submission
        handleSubmit(e as any);
      }
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!userInput.trim()) return;
    
    // If no thread exists, create one first
    if (!threadId) {
      try {
        // 1. Create new thread
        const createEndpoint = API_ENDPOINTS.createThread();
        const res = await fetch(createEndpoint, {
          method: "POST",
        });
        const data = await res.json();
        const newThreadId = data.threadId;
        
        // 2. Set thread ID
        setThreadId(newThreadId);
        setIsNewThread(true);
        
        // 3. Save to history with default name
        const defaultName = getDefaultThreadName();
        const historyEndpoint = API_ENDPOINTS.createThreadHistory();
        await fetch(historyEndpoint, {
          method: "POST",
          body: JSON.stringify({ 
            threadId: newThreadId,
            name: defaultName,
            isGroup: false
          }),
          headers: {
            'Content-Type': 'application/json',
          }
        });
        
        // 4. Refresh thread list
        if (threadListRef.current) {
          await threadListRef.current.fetchThreads();
        }
        
        // 5. Now send the message with the new threadId
        // Need to use newThreadId directly since state may not have updated yet
        const firstMessageText = userInput; // Save for title generation
        
        // FIX: Append to messages instead of replacing (same pattern as line 607)
        setMessages((prevMessages) => {
          const newMessages: MessageProps[] = [
            ...prevMessages,
            { role: "user" as const, text: userInput }
          ];
          // Update ref to prevent polling from overwriting
          lastMessageCountRef.current = newMessages.length;
          return newMessages;
        });
        setUserInput("");
        setInputDisabled(true);
        setShouldScroll(true);
        
        // Send message immediately with new thread ID
        await sendMessage(userInput, newThreadId);
        
        // 6. Generate title for the new thread
        try {
          console.log("Generating title for new thread:", newThreadId);
          const titleEndpoint = API_ENDPOINTS.generateTitle(newThreadId);
          const titleResponse = await fetch(titleEndpoint, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              message: firstMessageText,
            }),
          });

          if (titleResponse.ok) {
            const titleData = await titleResponse.json();
            const generatedTitle = titleData.title;
            console.log("Generated title:", generatedTitle);

            // Update thread name in history
            const updateEndpoint = API_ENDPOINTS.updateThread();
            await fetch(updateEndpoint, {
              method: "PUT",
              headers: {
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                threadId: newThreadId,
                newName: generatedTitle,
              }),
            });

            // Refresh thread list to show updated title
            if (threadListRef.current) {
              await threadListRef.current.fetchThreads();
            }
            
            console.log("✅ Thread title updated successfully");
          } else {
            console.error("Failed to generate title, status:", titleResponse.status);
          }
        } catch (titleError) {
          console.error("Error generating title:", titleError);
          // Don't fail the whole operation if title generation fails
        }
        
        setIsNewThread(false); // Mark thread as no longer new
        
      } catch (error) {
        console.error('Failed to create thread:', error);
        return;
      }
    } else {
      // Thread exists, just send message normally
      // CRITICAL: Add user message to state FIRST, then send to API
      setMessages((prevMessages) => {
        const newMessages: MessageProps[] = [
          ...prevMessages,
          { role: "user" as const, text: userInput },
        ];
        // Update ref to prevent polling from overwriting
        lastMessageCountRef.current = newMessages.length;
        return newMessages;
      });
      setUserInput("");
      setInputDisabled(true);
      setShouldScroll(true);
      
      // Send after adding to state
      sendMessage(userInput);
    }
  };

  /* Stream Event Handlers */

  // textCreated - create new assistant message
  const handleTextCreated = () => {
    appendMessage("assistant", "");
    setShouldScroll(true); // 添加新消息时滚动
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
    appendToLastMessage(`\n![${image.file_id}](/api/files/${image.file_id})\n`);
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
          // 使用完整的文件路径
          const fullPath = process.env.NODE_ENV === 'development' 
            ? `http://localhost:3000/api/files/${annotation.file_path.file_id}`
            : `/api/files/${annotation.file_path.file_id}`;
          
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
      const newMessages = (data.messages || []).map((msg: any) => ({
        role: msg.role,
        text: msg.content?.[0]?.text?.value || msg.content || '',
        annotations: msg.content?.[0]?.text?.annotations || []
      }));
      
      console.log(`[LOAD] Parsed ${newMessages.length} messages for thread: ${targetThreadId}`);
      
      if (isInitialLoad) {
        // On initial load (thread switch), always use backend state
        setMessages(newMessages);
        lastMessageCountRef.current = newMessages.length;
        setIsNewThread(newMessages.length === 0);
        if (newMessages.length > 0) {
          setShouldScroll(true);
        }
      } else {
        // On polling, only update if backend has MORE messages than local state
        if (newMessages.length > lastMessageCountRef.current) {
          console.log(`[LOAD] Updating: backend has ${newMessages.length}, local had ${lastMessageCountRef.current}`);
          setMessages(newMessages);
          lastMessageCountRef.current = newMessages.length;
          setIsNewThread(newMessages.length === 0);
          setShouldScroll(true);
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
    // Thread will be created when user sends first message
    setThreadId(null);
    setMessages([]); 
    setIsGroupConversation(false);
    setIsNewThread(true);
    setUserInput("");
    lastMessageCountRef.current = 0;
  };

  const handleThreadSelect = (selectedThreadId: string | null, isGroup: boolean) => {
    // CRITICAL: Stop any active polling IMMEDIATELY before changing state
    if (pollingIntervalRef.current) {
      clearInterval(pollingIntervalRef.current);
      pollingIntervalRef.current = null;
    }
    
    if (selectedThreadId === null) {
      // Show welcome screen - clear the current thread
      setThreadId("");
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
      
      // 3. Load messages directly (passing selectedThreadId to avoid stale state)
      loadThread(selectedThreadId, true);
    }
  };

  const handleJoinTeam = async () => {
    if (!joinThreadInput.trim()) return;
    
    try {
      const defaultName = getDefaultThreadName();
      const historyEndpoint = API_ENDPOINTS.createThreadHistory();
      const response = await fetch(historyEndpoint, {
        method: 'POST',
        body: JSON.stringify({ 
          threadId: joinThreadInput,
          name: defaultName,
          isGroup: true 
        }),
        headers: {
          'Content-Type': 'application/json'
        }
      });
  
      if (!response.ok) throw new Error('Failed to join team chat');
      
      setShowJoinModal(false);
      setJoinThreadInput('');
      handleThreadSelect(joinThreadInput, true);
      
      // 刷新线程列表
      if (threadListRef.current) {
        await threadListRef.current.fetchThreads();
      }
    } catch (error) {
      console.error('Error joining team chat:', error);
    }
  };

  return (
    <div className={styles.container}>
      <div className={styles.leftPanel}>
        <ThreadList 
          ref={threadListRef}
          currentThreadId={threadId}
          onThreadSelect={handleThreadSelect}
        />
      </div>
    <div className={styles.chatContainer}>
      <div className={styles.messages}>
        {!threadId ? (
          <WelcomeMessage />
        ) : (
          <>
            {messages.map((msg, index) => (
              <Message key={index} role={msg.role} text={msg.text} annotations={msg.annotations} />
            ))}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>
      <form
        onSubmit={handleSubmit}
        className={`${styles.inputForm} ${styles.clearfix}`}
      >
        <button
          type="button" 
          onClick={createNewThread}
          className={styles.newChatBtn}
        >
          New Chat
        </button>
        <button
          type="button"
          onClick={() => setShowJoinModal(true)}
          className={styles.newChatBtn}
        >
          Join Team Chat
        </button>
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
        <textarea
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
        >
          Send
        </button>
      </form>
    </div>
    </div>
  );
};

export default Chat;
