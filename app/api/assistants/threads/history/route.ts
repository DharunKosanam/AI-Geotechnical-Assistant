import { openai } from "@/app/openai";
import { getDatabase } from "@/lib/mongodb";

const USER_ID = 'default-user'; // Hardcoded user ID

// Get conversation history from MongoDB
export async function GET() {
  try {
    const db = await getDatabase();
    const conversations = await db
      .collection('conversations')
      .find({ userId: USER_ID })
      .sort({ createdAt: -1 })
      .toArray();

    return Response.json({ threads: conversations });
  } catch (error) {
    console.error('Error fetching conversations:', error);
    return Response.json(
      { error: error.message || 'Failed to fetch conversations', threads: [] },
      { status: 500 }
    );
  }
}

// Save new conversation to MongoDB
export async function POST(request) {
  try {
    const { threadId, name, isGroup = false } = await request.json();
    
    if (!threadId) {
      return Response.json(
        { error: 'Thread ID is required' },
        { status: 400 }
      );
    }

    const db = await getDatabase();
    const conversationsCollection = db.collection('conversations');
    
    // Check if thread already exists
    const existingThread = await conversationsCollection.findOne({ 
      userId: USER_ID, 
      threadId 
    });
    
    if (existingThread) {
      return Response.json({ success: true, message: 'Thread already exists' });
    }
    
    // Insert new conversation
    const conversation = {
      userId: USER_ID,
      threadId,
      name: name || new Date().toLocaleString(),
      isGroup,
      createdAt: new Date(),
      updatedAt: new Date()
    };
    
    await conversationsCollection.insertOne(conversation);
    
    return Response.json({ success: true });
  } catch (error) {
    console.error('Save thread error:', error);
    return Response.json(
      { error: error.message || 'Failed to save thread' },
      { status: 500 }
    );
  }
}

// Update conversation name or group status
export async function PUT(request) {
  try {
    const { threadId, newName, isGroup, nickname } = await request.json();
    
    const db = await getDatabase();
    const conversationsCollection = db.collection('conversations');
    
    if (nickname !== undefined) {
      // For now, ignore nickname updates since we're using hardcoded user
      return Response.json({ success: true, message: 'Nickname not implemented yet' });
    }
    
    if (!threadId) {
      return Response.json(
        { error: 'Thread ID is required' },
        { status: 400 }
      );
    }
    
    // Build update object
    const updateFields: {
      updatedAt: Date;
      name?: string;
      isGroup?: boolean;
    } = {
      updatedAt: new Date()
    };
    
    if (newName !== undefined) {
      updateFields.name = newName;
    }
    
    if (isGroup !== undefined) {
      updateFields.isGroup = isGroup;
    }
    
    const result = await conversationsCollection.updateOne(
      { userId: USER_ID, threadId },
      { $set: updateFields }
    );
    
    if (result.matchedCount === 0) {
      return Response.json(
        { error: 'Thread not found' },
        { status: 404 }
      );
    }
    
    return Response.json({ success: true });
  } catch (error) {
    console.error('Update thread error:', error);
    return Response.json(
      { error: error.message || 'Failed to update thread' },
      { status: 500 }
    );
  }
}

// Delete conversation
export async function DELETE(request: Request) {
  try {
    const { threadId, isGroup } = await request.json();
    
    if (!threadId) {
      return Response.json({ error: 'Thread ID is required' }, { status: 400 });
    }
    
    // If not a group thread, try to delete from OpenAI
    if (!isGroup) {
      try {
        await openai.beta.threads.del(threadId);
      } catch (openaiError) {
        // Thread might not exist (404) - that's fine, we still want to remove it from database
        if (openaiError.status !== 404 && openaiError.statusCode !== 404) {
          console.error('OpenAI thread deletion failed:', openaiError);
        }
      }
    }
    
    // Delete from MongoDB
    const db = await getDatabase();
    await db.collection('conversations').deleteOne({ 
      userId: USER_ID, 
      threadId 
    });
    
    return Response.json({ success: true });
  } catch (error) {
    console.error('Delete thread error:', error);
    return Response.json(
      { error: error.message || 'Failed to delete thread' },
      { status: 500 }
    );
  }
}
