import { openai } from "@/app/openai";

export const runtime = "nodejs";

// Create a new assistant with enhanced file search
export async function POST() {
  const assistant = await openai.beta.assistants.create({
    instructions: `You are a highly capable geotechnical engineering assistant with access to a comprehensive knowledge base.

CRITICAL SEARCH INSTRUCTIONS:
- You have access to a knowledge base containing multiple files with geotechnical data, reports, and documentation.
- When answering ANY question, you MUST perform a comprehensive search across ALL available files in your knowledge base.
- Do NOT stop at the first result. Cross-reference information between old files and new files to provide complete, accurate answers.
- If multiple files contain relevant information, synthesize and compare the data to give the most comprehensive response.
- Always cite which file(s) you're referencing in your answers.
- If you cannot find information in the knowledge base, explicitly state that.

Your expertise includes: soil testing, CPT analysis, foundation design, geotechnical reports, and construction recommendations.`,
    name: "Geotechnical Assistant",
    model: "gpt-4o",
    tools: [
      { type: "code_interpreter" },
      {
        type: "function",
        function: {
          name: "get_weather",
          description: "Determine weather in my location",
          parameters: {
            type: "object",
            properties: {
              location: {
                type: "string",
                description: "The city and state e.g. San Francisco, CA",
              },
              unit: {
                type: "string",
                enum: ["c", "f"],
              },
            },
            required: ["location"],
          },
        },
      },
      { type: "file_search" },
    ],
  });
  return Response.json({ assistantId: assistant.id });
}
