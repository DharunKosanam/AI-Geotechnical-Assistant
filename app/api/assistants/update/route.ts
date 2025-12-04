import { openai } from "@/app/openai";
import { assistantId } from "@/app/assistant-config";

export const runtime = "nodejs";

// Update existing assistant with enhanced file search configuration
export async function POST() {
  console.log("\n========== UPDATING ASSISTANT CONFIGURATION ==========");
  
  try {
    if (!assistantId) {
      return Response.json(
        { error: "Assistant ID not found in configuration" },
        { status: 500 }
      );
    }

    console.log("Updating Assistant ID:", assistantId);

    const updatedAssistant = await openai.beta.assistants.update(assistantId, {
      instructions: `You are a highly capable geotechnical engineering assistant with access to a comprehensive knowledge base.

CRITICAL SEARCH INSTRUCTIONS:
- You have access to a knowledge base containing multiple files with geotechnical data, reports, and documentation.
- When answering ANY question, you MUST perform a comprehensive search across ALL available files in your knowledge base.
- Do NOT stop at the first result. Cross-reference information between old files and new files to provide complete, accurate answers.
- If multiple files contain relevant information, synthesize and compare the data to give the most comprehensive response.
- Always cite which file(s) you're referencing in your answers.
- If you cannot find information in the knowledge base, explicitly state that.

Your expertise includes: soil testing, CPT analysis, foundation design, geotechnical reports, and construction recommendations.`,
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

    console.log("✅ Assistant updated successfully!");
    console.log("Assistant name:", updatedAssistant.name);
    console.log("Model:", updatedAssistant.model);
    console.log("Tools:", updatedAssistant.tools.map(t => t.type).join(", "));
    console.log("========== END UPDATE ==========\n");

    return Response.json({ 
      success: true,
      assistantId: updatedAssistant.id,
      name: updatedAssistant.name,
      model: updatedAssistant.model,
      tools: updatedAssistant.tools,
      message: "Assistant configuration updated with enhanced file search capabilities"
    });

  } catch (error) {
    console.error("Error updating assistant:", error);
    return Response.json(
      { error: error.message || "Failed to update assistant" },
      { status: 500 }
    );
  }
}

