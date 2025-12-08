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

type MessageProps = {
  role: "user" | "assistant" | "code";
  text: string;
};

const UserMessage = ({ text }: { text: string }) => {
  return <div className={styles.userMessage}>{text}</div>;
};

const AssistantMessage = ({ text }: { text: string }) => {
  return (
    <div className={styles.assistantMessage}>
      <Markdown>{text}</Markdown>
    </div>
  );
};

const CodeMessage = ({ text }: { text: string }) => {
  return (
    <div className={styles.codeMessage}>
      {text.split("\n").map((line, index) => (
        <div key={index}>
          <span>{`${index + 1}. `}</span>
          {line}
        </div>
      ))}
    </div>
  );
};

const Message = ({ role, text }: MessageProps) => {
  switch (role) {
    case "user":
      return <UserMessage text={text} />;
    case "assistant":
      return <AssistantMessage text={text} />;
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

  // 添加一个生成默认名称的函数
  const getDefaultThreadName = () => {
    return new Date().toLocaleString();
  };

  // 修改滚动效果
  useEffect(() => {
    if (shouldScroll) {
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      setShouldScroll(false);
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

  const generateAndUpdateTitle = async (firstMessage: string) => {
    try {
      const response = await fetch(`/api/assistants/threads/${threadId}/title`, {
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
      await fetch(`/api/assistants/threads/history`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          threadId: threadId,
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

  const sendMessage = async (text, targetThreadId = null) => {
    const actualThreadId = targetThreadId || threadId;
    const isFirstMessage = isNewThread && messages.length === 0;
    
    try {
      const response = await fetch(
        `/api/assistants/threads/${actualThreadId}/messages`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            content: text,
          }),
        }
      );

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
        generateAndUpdateTitle(text).catch(error => {
          console.error("Error generating title:", error);
        });
      }

      const stream = AssistantStream.fromReadableStream(response.body);
      handleReadableStream(stream);
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
        const res = await fetch(`/api/assistants/threads`, {
          method: "POST",
        });
        const data = await res.json();
        const newThreadId = data.threadId;
        
        // 2. Set thread ID
        setThreadId(newThreadId);
        setIsNewThread(true);
        
        // 3. Save to history with default name
        const defaultName = getDefaultThreadName();
        await fetch(`/api/assistants/threads/history`, {
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
          const titleResponse = await fetch(`/api/assistants/threads/${newThreadId}/title`, {
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
            await fetch(`/api/assistants/threads/history`, {
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

  const loadThread = async (threadId: string) => {
    try {
      setThreadId(threadId);
      // use new history endpoint to retrieve messages
      const response = await fetch(`/api/assistants/threads/${threadId}/history`);
      
      if (response.status === 404) {
        // Thread not found - likely created with different API key or deleted
        const errorData = await response.json();
        console.warn('Thread not found:', errorData.error);
        
        // Remove invalid thread from the list
        try {
          await fetch('/api/assistants/threads/history', {
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
      const messages = data.messages.map(msg => ({
        role: msg.role,
        text: msg.content[0]?.text?.value || ''
      }))
      .reverse();
      setMessages(messages);
      // If thread has messages, it's not a new thread
      setIsNewThread(messages.length === 0);
      // 加载历史消息时不触发滚动
      setShouldScroll(false);      
    } catch (error) {
      console.error('Failed loading history conversation:', error);
      // On error, create a new thread to continue working
      setMessages([]);
      await createNewThread();
    }
  }
  const createNewThread = () => {
    // Simply reset to welcome state
    // Thread will be created when user sends first message
    setThreadId(null);
    setMessages([]); 
    setIsGroupConversation(false);
    setIsNewThread(true);
    setUserInput("");
  };

  const handleThreadSelect = (threadId: string | null, isGroup: boolean) => {
    if (threadId === null) {
      // Show welcome screen - clear the current thread
      setThreadId("");
      setMessages([]);
      setIsGroupConversation(false);
      setIsNewThread(false);
    } else {
      // Load the selected thread
      setThreadId(threadId);
      setIsGroupConversation(isGroup);
      loadThread(threadId);
    }
  };

  const handleJoinTeam = async () => {
    if (!joinThreadInput.trim()) return;
    
    try {
      const defaultName = getDefaultThreadName();
      const response = await fetch('/api/assistants/threads/history', {
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
              <Message key={index} role={msg.role} text={msg.text} />
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
