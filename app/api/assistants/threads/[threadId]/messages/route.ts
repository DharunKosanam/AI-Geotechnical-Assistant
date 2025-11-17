import { assistantId } from "@/app/assistant-config";
import { openai } from "@/app/openai";

export const runtime = "nodejs";

// Send a new message to a thread
export async function POST(request, { params }) {
  try {
    // Handle Next.js 14 (sync) and 15+ (async) params
    const resolvedParams = params instanceof Promise ? await params : params;
    const threadId = resolvedParams.threadId;
    
    const { content } = await request.json();

    if (!content) {
      return Response.json(
        { error: "Content is required" },
        { status: 400 }
      );
    }

    if (!threadId) {
      return Response.json(
        { error: "Thread ID is required" },
        { status: 400 }
      );
    }

    if (!assistantId) {
      return Response.json(
        { error: "Assistant ID is not configured" },
        { status: 500 }
      );
    }

    try {
      await openai.beta.threads.messages.create(threadId, {
        role: "user",
        content: content,
      });
    } catch (createError) {
      console.error("Error creating message:", createError);
      
      // Handle specific OpenAI errors
      if (createError.code === 'invalid_api_key' || createError.type === 'invalid_request_error') {
        return Response.json(
          { 
            error: "Invalid API key. Please check your OPENAI_API_KEY in the .env file.",
            details: createError.message 
          },
          { status: 401 }
        );
      }
      
      if (createError.status === 404) {
        return Response.json(
          { 
            error: "Thread not found. Please create a new chat.",
            details: createError.message 
          },
          { status: 404 }
        );
      }
      
      throw createError; // Re-throw to be caught by outer catch
    }

    try {
      const stream = openai.beta.threads.runs.stream(threadId, {
        assistant_id: assistantId,
      });

      return new Response(stream.toReadableStream(), {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        },
      });
    } catch (streamError) {
      console.error("Error creating stream:", streamError);
      
      // Handle specific OpenAI errors
      if (streamError.code === 'invalid_api_key' || streamError.type === 'invalid_request_error') {
        return Response.json(
          { 
            error: "Invalid API key. Please check your OPENAI_API_KEY in the .env file.",
            details: streamError.message 
          },
          { status: 401 }
        );
      }
      
      throw streamError; // Re-throw to be caught by outer catch
    }
  } catch (error) {
    console.error("Error in messages route:", error);
    
    // Handle specific OpenAI API errors
    if (error.code === 'invalid_api_key' || error.type === 'invalid_request_error') {
      return Response.json(
        { 
          error: "Invalid API key. Please check your OPENAI_API_KEY in the .env file.",
          details: error.message 
        },
        { status: 401 }
      );
    }
    
    if (error.status === 401 || error.statusCode === 401) {
      return Response.json(
        { 
          error: "Authentication failed. Please check your OpenAI API key.",
          details: error.message 
        },
        { status: 401 }
      );
    }
    
    if (error.status === 404 || error.statusCode === 404) {
      return Response.json(
        { 
          error: "Thread or assistant not found. Please create a new chat.",
          details: error.message 
        },
        { status: 404 }
      );
    }
    
    return Response.json(
      { 
        error: error.message || "Internal server error",
        details: error.code || error.type || "unknown_error"
      },
      { status: error.status || error.statusCode || 500 }
    );
  }
}
