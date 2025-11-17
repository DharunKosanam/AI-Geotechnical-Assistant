import { openai } from "@/app/openai";

export async function GET(request, { params }) {
  try {
    // Handle Next.js 14 (sync) and 15+ (async) params
    const resolvedParams = params instanceof Promise ? await params : params;
    const threadId = resolvedParams.threadId;
    
    if (!threadId) {
      return Response.json({ error: 'Thread ID is required' }, { status: 400 });
    }
    
    const messages = await openai.beta.threads.messages.list(threadId);
    return Response.json({
      threadId,
      messages: messages.data
    });
  } catch (error) {
    console.error('Failed to load thread history:', error);
    
    // Handle 404 - thread not found (likely created with different API key or deleted)
    if (error.status === 404 || error.statusCode === 404 || error.code === 'not_found') {
      const resolvedParams = params instanceof Promise ? await params : params;
      const threadId = resolvedParams?.threadId;
      
      return Response.json({ 
        error: 'Thread not found. It may have been created with a different API key or deleted.',
        threadId: threadId,
        notFound: true
      }, { status: 404 });
    }
    
    // Handle other errors
    const resolvedParams = params instanceof Promise ? await params : params;
    const threadId = resolvedParams?.threadId;
    
    return Response.json({ 
      error: error.message || 'Failed to load thread history',
      threadId: threadId
    }, { status: error.status || error.statusCode || 500 });
  }
}