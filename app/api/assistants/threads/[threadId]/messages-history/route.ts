import { openai } from "@/app/openai";
import { OpenAI } from "openai";

export const runtime = "nodejs";

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

    // Transform messages to simpler format with Type Guard
    const messages = threadMessages.data.map((message) => {
      // Safe filtering with Type Guard
      const textContent = message.content
        .filter((c): c is OpenAI.Beta.Threads.Messages.TextContentBlock => c.type === 'text')
        .map((c) => c.text.value)
        .join('\n');

      return {
        role: message.role,
        text: textContent,
      };
    });

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

