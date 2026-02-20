import { openai } from "@/app/openai";

export async function POST(request: Request, context: any) {
  const { threadId } = await context.params;
  const { toolCallOutputs, runId } = await request.json();

  const stream = openai.beta.threads.runs.submitToolOutputsStream(
    threadId,
    runId,
    { tool_outputs: toolCallOutputs }
  );

  return new Response(stream.toReadableStream());
}
