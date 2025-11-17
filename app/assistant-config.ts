// Get assistant ID from environment variable or use default
export let assistantId = process.env.OPENAI_ASSISTANT_ID || "asst_mbSUwnJtQTwDYQYESHzeiHtM";

// If assistant ID is empty string, try to get from env again
if (assistantId === "") {
  assistantId = process.env.OPENAI_ASSISTANT_ID || "";
}
