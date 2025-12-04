import { openai } from "@/app/openai";

export const runtime = "nodejs";

// Type guard to filter text content blocks
function isTextContent(content: any): content is { type: "text"; text: { value: string } } {
  return content.type === "text";
}

// Get message history for a thread (for SWR polling)
export async function GET(request, { params }) {
  try {
    // Handle Next.js 14 (sync) and 15+ (async) params
    const resolvedParams = params instanceof Promise ? await params : params;
    const threadId = resolvedParams.threadId;

    if (!threadId) {
      return Response.json(
        { error: "Thread ID is required" },
        { status: 400 }
      );
    }

    // Fetch messages from OpenAI
    const threadMessages = await openai.beta.threads.messages.list(threadId, {
      order: "asc",
      limit: 50, // Limit to last 50 messages for performance
    });

    // Transform messages to simpler format
    const messages = threadMessages.data.map((message) => ({
      role: message.role,
      text: message.content
        .map((content) => {
          if (content.type === "text") {
            return content.text.value;
          }
          return "";
        })
        .filter((text) => text !== "")
        .join("\n"),
    }));

    return Response.json({ messages });

  } catch (error) {
    console.error("Error fetching message history:", error);
    
    if (error.status === 404) {
      return Response.json(
        { error: "Thread not found" },
        { status: 404 }
      );
    }

    return Response.json(
      { error: error.message || "Failed to fetch messages" },
      { status: 500 }
    );
  }
}

