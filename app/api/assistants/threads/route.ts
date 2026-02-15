import { openai } from "@/app/openai";

export const runtime = "nodejs";

// Create a new thread
export async function POST() {
  if (!openai) {
    // When using Python backend, generate a simple thread ID locally
    const threadId = `thread_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`;
    return Response.json({ threadId });
  }
  const thread = await openai.beta.threads.create();
  return Response.json({ threadId: thread.id });
}
