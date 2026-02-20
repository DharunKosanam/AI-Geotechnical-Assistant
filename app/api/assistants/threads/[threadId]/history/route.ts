import { openai } from "@/app/openai";

export async function GET(request: Request, context: any) {
  let threadId: string | undefined;
  try {
    const params = await context.params;
    threadId = params.threadId;
    
    if (!threadId) {
      return Response.json({ error: 'Thread ID is required' }, { status: 400 });
    }
    
    const messages = await openai.beta.threads.messages.list(threadId);
    return Response.json({
      threadId,
      messages: messages.data
    });
  } catch (error: any) {
    console.error('Failed to load thread history:', error);
    
    if (error.status === 404 || error.statusCode === 404 || error.code === 'not_found') {
      return Response.json({ 
        error: 'Thread not found. It may have been created with a different API key or deleted.',
        threadId,
        notFound: true
      }, { status: 404 });
    }
    
    return Response.json({ 
      error: error.message || 'Failed to load thread history',
      threadId
    }, { status: error.status || error.statusCode || 500 });
  }
}