"use client";

import React, { useState, useEffect, useRef } from "react";
import useSWR from "swr";
import styles from "./chat.module.css";
import { AssistantStream } from "openai/lib/AssistantStream";
import Markdown from "react-markdown";
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
            components={{
              // Style emphasis/italic elements (our citations) with smaller gray text
              em: ({node, ...props}) => (
                <em style={{ 
                  color: '#6b7280', 
                  fontSize: '0.875em',
                  fontStyle: 'italic',
                  fontWeight: '500'
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
  const [messages, setMessages] = useState([]);
  const [inputDisabled, setInputDisabled] = useState(false);
  const [threadId, setThreadId] = useState("");
  const [isGroupConversation, setIsGroupConversation] = useState(false);
  const threadListRef = useRef(null);
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

  // Update messages when SWR fetches new data
  useEffect(() => {
    if (messageData?.messages && isGroupConversation) {
      console.log("SWR: New messages received", messageData.messages.length);
      setMessages(messageData.messages);
      setShouldScroll(true);
    }
  }, [messageData, isGroupConversation]);

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

  const sendMessage = async (text, targetThreadId = null) => {
    const actualThreadId = targetThreadId || threadId;
    const isFirstMessage = isNewThread && messages.length === 0;
    
    try {
      // Get API endpoint based on configuration (Python or Next.js)
      const endpoint = API_ENDPOINTS.sendMessage(actualThreadId);
      const requestBody = getMessageRequestBody(text, actualThreadId);
      
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(requestBody),
      });

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

      if (!response.body) {
        console.error("Response body is null");
        appendMessage("assistant", "\n\n[Error: No response from server]");
        setInputDisabled(false);
        return;
      }

      // Generate title for first message in new thread
      if (isFirstMessage) {
        setIsNewThread(false);
        // Generate title asynchronously after starting the stream
        generateAndUpdateTitle(text, actualThreadId).catch(error => {
          console.error("Error generating title:", error);
        });
      }

      // Parse SSE stream manually for Python backend
      if (isPythonBackend()) {
        await handleSSEStream(response.body);
      } else {
        const stream = AssistantStream.fromReadableStream(response.body);
        handleReadableStream(stream);
      }
    } catch (error) {
      console.error("Error sending message:", error);
      appendMessage("assistant", `\n\n[Error: ${error.message || "Failed to send message"}]`);
      setInputDisabled(false);
    }
  };

  const submitActionResult = async (runId, toolCallOutputs) => {
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

  const handleSubmit = async (e) => {
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
        
        setMessages([{ role: "user", text: userInput }]);
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
      sendMessage(userInput);
      setMessages((prevMessages) => [
        ...prevMessages,
        { role: "user", text: userInput },
      ]);
      setUserInput("");
      setInputDisabled(true);
      setShouldScroll(true);
    }
  };

  /* Stream Event Handlers */

  // textCreated - create new assistant message
  const handleTextCreated = () => {
    appendMessage("assistant", "");
    setShouldScroll(true); // 添加新消息时滚动
  };

  // textDelta - append text to last assistant message
  const handleTextDelta = (delta) => {
    if (delta.value != null) {
      appendToLastMessage(delta.value);
    };
    if (delta.annotations != null) {
      annotateLastMessage(delta.annotations);
    }
  };

  // imageFileDone - show image in chat
  const handleImageFileDone = (image) => {
    appendToLastMessage(`\n![${image.file_id}](/api/files/${image.file_id})\n`);
  }

  // toolCallCreated - log new tool call
  const toolCallCreated = (toolCall) => {
    if (toolCall.type != "code_interpreter") return;
    appendMessage("code", "");
  };

  // toolCallDelta - log delta and snapshot for the tool call
  const toolCallDelta = (delta, snapshot) => {
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

  const appendToLastMessage = (text) => {
    setMessages((prevMessages) => {
      const lastMessage = prevMessages[prevMessages.length - 1];
      const updatedLastMessage = {
        ...lastMessage,
        text: lastMessage.text + text,
      };
      return [...prevMessages.slice(0, -1), updatedLastMessage];
    });
  };

  const appendMessage = (role, text) => {
    setMessages((prevMessages) => [...prevMessages, { role, text }]);
  };

  const annotateLastMessage = (annotations) => {
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

  const loadThread = async (threadId: string, isInitialLoad = true) => {
    try {
      if (isInitialLoad) {
        setThreadId(threadId);
      }
      // use new history endpoint to retrieve messages
      const endpoint = API_ENDPOINTS.getThreadMessages(threadId);
      const response = await fetch(endpoint);
      
      if (response.status === 404) {
        // Thread not found - likely created with different API key or deleted
        const errorData = await response.json();
        console.warn('Thread not found:', errorData.error);
        
        // Remove invalid thread from the list
        try {
          const deleteEndpoint = API_ENDPOINTS.deleteThread();
          await fetch(deleteEndpoint, {
            method: 'DELETE',
            body: JSON.stringify({ 
              threadId: threadId,
              isGroup: false // Assume not a group thread for cleanup
            }),
            headers: {
              'Content-Type': 'application/json',
            },
          });
          
          // Refresh thread list
          if (threadListRef.current) {
            await threadListRef.current.fetchThreads();
          }
        } catch (deleteError) {
          console.error('Failed to remove invalid thread:', deleteError);
        }
        
        // Clear the invalid thread and create a new one
        setMessages([]);
        setThreadId(null);
        
        // Create a new thread automatically
        await createNewThread();
        return;
      }
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      const newMessages = data.messages.map(msg => ({
        role: msg.role,
        text: msg.content[0]?.text?.value || '',
        annotations: msg.content[0]?.text?.annotations || []
      }));
      // Messages are already in chronological order (oldest first) from backend
      
      // Only update if there are new messages (to prevent flickering)
      if (newMessages.length !== lastMessageCountRef.current || isInitialLoad) {
        const hadNewMessages = newMessages.length > lastMessageCountRef.current;
        setMessages(newMessages);
        lastMessageCountRef.current = newMessages.length;
        
        // If thread has messages, it's not a new thread
        setIsNewThread(newMessages.length === 0);
        
        // Scroll to bottom on initial load or when new messages arrive
        if (newMessages.length > 0) {
          setShouldScroll(true);
        }
      }
    } catch (error) {
      console.error('Failed loading history conversation:', error);
      // On error, create a new thread to continue working
      if (isInitialLoad) {
        setMessages([]);
        await createNewThread();
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

  const handleThreadSelect = (threadId: string | null, isGroup: boolean) => {
    if (threadId === null) {
      // Show welcome screen - clear the current thread
      setThreadId("");
      setMessages([]);
      setIsGroupConversation(false);
      setIsNewThread(false);
      lastMessageCountRef.current = 0;
    } else {
      // Load the selected thread
      setThreadId(threadId);
      setIsGroupConversation(isGroup);
      lastMessageCountRef.current = 0; // Reset message count for new thread
      loadThread(threadId);
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
        <input
          type="text"
          className={styles.input}
          value={userInput}
          onChange={(e) => setUserInput(e.target.value)}
          placeholder="Enter your question"
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
