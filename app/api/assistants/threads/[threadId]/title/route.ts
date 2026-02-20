import { openai } from "@/app/openai";

export const runtime = "nodejs";

// Generate a title for a thread based on the first message
export async function POST(request: Request, context: any) {
  try {
    const { threadId } = await context.params;
    
    const { message } = await request.json();

    if (!message || typeof message !== 'string') {
      return Response.json(
        { error: "Message is required" },
        { status: 400 }
      );
    }

    if (!threadId) {
      return Response.json(
        { error: "Thread ID is required" },
        { status: 400 }
      );
    }

    if (!openai) {
      // Without OpenAI, generate a simple title from the message
      const simpleTitle = message.length > 50 ? message.substring(0, 47) + "..." : message;
      return Response.json({ title: simpleTitle });
    }

    // Generate a concise title (max 60 characters) from the first message
    const completion = await openai.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [
        {
          role: "system",
          content: "You are a helpful assistant that generates short, descriptive titles for chat conversations. Generate a title based on the user's first message. The title should be concise (maximum 60 characters), descriptive, and capture the main topic or question. Return only the title text, nothing else."
        },
        {
          role: "user",
          content: `Generate a title for this conversation based on the first message: "${message}"`
        }
      ],
      max_tokens: 30,
      temperature: 0.7,
    });

    const title = completion.choices[0]?.message?.content?.trim() || "New Chat";

    // Limit title length to 60 characters
    const finalTitle = title.length > 60 ? title.substring(0, 57) + "..." : title;

    return Response.json({ title: finalTitle });
  } catch (error) {
    console.error("Error generating title:", error);
    return Response.json(
      { error: error.message || "Failed to generate title" },
      { status: 500 }
    );
  }
}

