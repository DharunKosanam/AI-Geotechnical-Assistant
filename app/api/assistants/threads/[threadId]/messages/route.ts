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

    // STEP 1: Check for active runs and cancel them to prevent race condition
    try {
      console.log("Checking for active runs on thread:", threadId);
      const runs = await openai.beta.threads.runs.list(threadId, {
        limit: 5, // Check last 5 runs
      });

      // Find any active runs
      const activeRun = runs.data.find(run => 
        run.status === 'in_progress' || 
        run.status === 'queued' || 
        run.status === 'requires_action'
      );

      if (activeRun) {
        console.log(`⚠️  Found active run (${activeRun.status}): ${activeRun.id}. Cancelling...`);
        
        try {
          await openai.beta.threads.runs.cancel(threadId, activeRun.id);
          console.log(`✅ Cancelled run ${activeRun.id}`);
          
          // Wait 1 second for cancellation to process
          await new Promise(resolve => setTimeout(resolve, 1000));
        } catch (cancelError) {
          console.warn("Could not cancel run:", cancelError.message);
          // Continue anyway - the run might have completed naturally
        }
      }
    } catch (checkError) {
      console.warn("Error checking for active runs:", checkError.message);
      // Continue anyway - better to try than to fail completely
    }

    // STEP 2: Now add the message
    try {
      await openai.beta.threads.messages.create(threadId, {
        role: "user",
        content: content,
      });
      console.log("✅ Message added to thread successfully");
    } catch (createError) {
      console.error("❌ Error creating message:", createError);
      
      // Handle 401 errors with explicit logging
      if (createError.status === 401 || createError.code === 'invalid_api_key') {
        console.error("🔑 API KEY INVALID OR MISSING!");
        console.error("Please check your OPENAI_API_KEY in the .env file");
        return Response.json(
          { 
            error: "Invalid API key. Please check your OPENAI_API_KEY in the .env file.",
            details: createError.message 
          },
          { status: 401 }
        );
      }
      
      if (createError.type === 'invalid_request_error') {
        console.error("Invalid request:", createError.message);
        return Response.json(
          { 
            error: "Invalid request to OpenAI API",
            details: createError.message 
          },
          { status: 400 }
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
      
      // Handle the "can't add message while run is active" error
      if (createError.status === 400 && createError.message?.includes('run')) {
        console.error("Race condition detected - active run still present");
        return Response.json(
          { 
            error: "AI is still processing. Please wait a moment and try again.",
            details: createError.message 
          },
          { status: 400 }
        );
      }
      
      throw createError; // Re-throw to be caught by outer catch
    }

    // STEP 3: Prepare vector store for file_search tool
    // NOTE: Knowledge Base (OPENAI_KNOWLEDGE_STORE_ID) is already attached to the Assistant
    // via OpenAI Dashboard, so we ONLY need to attach the user's file store here.
    // OpenAI API allows maximum 1 vector store in the array.

    // STEP 4: Start the streaming run
    try {
      console.log("Starting streaming run for thread:", threadId);
      
      const runConfig: any = {
        assistant_id: assistantId,
        // OPTIMIZATION: Truncate history to save tokens (only keep last 10 messages)
        truncation_strategy: {
          type: "last_messages",
          last_messages: 10,
        },
        // OPTIMIZATION: Limit response length to prevent expensive run-on answers
        max_completion_tokens: 1000,
        // OPTIMIZATION: Use faster, cheaper model if high reasoning not required
        // model: "gpt-4o-mini", // Uncomment to use cheaper model
      };

      // Only add tool_resources if user vector store exists
      // This overrides/attaches the user's file store for this specific run
      if (process.env.OPENAI_VECTOR_STORE_ID) {
        console.log("Attaching user vector store:", process.env.OPENAI_VECTOR_STORE_ID);
        runConfig.tool_resources = {
          file_search: {
            vector_store_ids: [process.env.OPENAI_VECTOR_STORE_ID]
          }
        };
      }
      
      const stream = openai.beta.threads.runs.stream(threadId, runConfig);

      return new Response(stream.toReadableStream(), {
        headers: {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          "Connection": "keep-alive",
        },
      });
    } catch (streamError) {
      console.error("❌ Error creating stream:", streamError);
      
      // Handle 401 errors with explicit logging
      if (streamError.status === 401 || streamError.code === 'invalid_api_key') {
        console.error("🔑 API KEY INVALID OR MISSING!");
        console.error("Please check your OPENAI_API_KEY in the .env file");
        return Response.json(
          { 
            error: "Invalid API key. Please check your OPENAI_API_KEY in the .env file.",
            details: streamError.message 
          },
          { status: 401 }
        );
      }
      
      if (streamError.type === 'invalid_request_error') {
        console.error("Invalid request:", streamError.message);
        return Response.json(
          { 
            error: "Invalid request to OpenAI API",
            details: streamError.message 
          },
          { status: 400 }
        );
      }
      
      throw streamError; // Re-throw to be caught by outer catch
    }
  } catch (error) {
    console.error("❌ Error in messages route:", error);
    console.error("Error type:", error.constructor.name);
    console.error("Error status:", error.status);
    
    // Handle 401 errors with explicit logging
    if (error.status === 401 || error.statusCode === 401 || error.code === 'invalid_api_key') {
      console.error("🔑 API KEY INVALID OR MISSING!");
      console.error("Please check your OPENAI_API_KEY in the .env file");
      console.error("Error details:", error.message);
      return Response.json(
        { 
          error: "Invalid API key. Please check your OPENAI_API_KEY in the .env file.",
          details: error.message 
        },
        { status: 401 }
      );
    }
    
    if (error.type === 'invalid_request_error') {
      console.error("Invalid request error:", error.message);
      return Response.json(
        { 
          error: "Invalid request to OpenAI API",
          details: error.message 
        },
        { status: 400 }
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
