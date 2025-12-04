import { openai } from "@/app/openai";

// upload files (batch) to assistant's vector store
export async function POST(request) {
  console.log("\n========== POST /api/assistants/files (BATCH) ==========");
  
  try {
    // Step 1: Validate Vector Store ID
    const vectorStoreId = process.env.OPENAI_VECTOR_STORE_ID;
    console.log("Step 1: Vector Store ID from env:", vectorStoreId);
    
    if (!vectorStoreId) {
      console.error("ERROR: Vector Store ID not found in environment variables");
      return Response.json(
        { error: "Vector Store ID not found. Please add OPENAI_VECTOR_STORE_ID to your .env file." },
        { status: 500 }
      );
    }

    // Step 2: Get ALL files from request
    const formData = await request.formData();
    const files = formData.getAll("files"); // Get all files (note: "files" plural)
    
    if (!files || files.length === 0) {
      console.error("ERROR: No files provided in the request");
      return Response.json(
        { error: "No files provided" },
        { status: 400 }
      );
    }
    
    console.log(`Step 2: Received ${files.length} file(s):`);
    files.forEach((file, idx) => {
      console.log(`  [${idx + 1}] ${file.name} (${file.size} bytes)`);
    });
    
    // Step 3: Upload ALL files to OpenAI in parallel
    console.log(`Step 3: Uploading ${files.length} file(s) to OpenAI in parallel...`);
    
    const uploadPromises = files.map(async (file, idx) => {
      console.log(`  [${idx + 1}] Uploading ${file.name}...`);
      const openaiFile = await openai.files.create({
        file: file,
        purpose: "assistants",
      });
      console.log(`  [${idx + 1}] ✅ Uploaded: ${file.name} → ID: ${openaiFile.id}`);
      return openaiFile.id;
    });
    
    const fileIds = await Promise.all(uploadPromises);
    console.log(`Step 3: All ${fileIds.length} file(s) uploaded successfully!`);
    console.log(`File IDs: ${fileIds.join(", ")}`);

    // Step 4: Add ALL files to Vector Store using BATCH API with polling
    console.log(`Step 4: Adding ${fileIds.length} file(s) to Vector Store using BATCH API...`);
    console.log("        (This may take a few seconds depending on file sizes)");
    
    const fileBatch = await openai.beta.vectorStores.fileBatches.createAndPoll(
      vectorStoreId,
      { file_ids: fileIds }
    );
    
    console.log("Step 4: Batch processing complete!");
    console.log(`  Status: ${fileBatch.status}`);
    console.log(`  Total files: ${fileBatch.file_counts.total}`);
    console.log(`  Completed: ${fileBatch.file_counts.completed}`);
    console.log(`  Failed: ${fileBatch.file_counts.failed}`);
    console.log(`  In progress: ${fileBatch.file_counts.in_progress}`);
    
    // Step 5: Check final status
    if (fileBatch.status === 'completed') {
      console.log("✅ SUCCESS: All files are ready and indexed in Vector Store");
      console.log("========== END POST /api/assistants/files ==========\n");
      
      return Response.json({ 
        success: true, 
        filesUploaded: fileBatch.file_counts.completed,
        filesFailed: fileBatch.file_counts.failed,
        fileIds: fileIds,
        status: fileBatch.status
      });
      
    } else if (fileBatch.status === 'failed') {
      console.error("❌ FAILED: Batch processing failed");
      
      return Response.json(
        { 
          error: "Batch file processing failed", 
          filesCompleted: fileBatch.file_counts.completed,
          filesFailed: fileBatch.file_counts.failed
        },
        { status: 500 }
      );
      
    } else if (fileBatch.status === 'cancelled') {
      console.error("❌ CANCELLED: Batch processing was cancelled");
      
      return Response.json(
        { error: "Batch file processing was cancelled" },
        { status: 500 }
      );
      
    } else {
      // Unexpected status or partial completion
      console.warn("⚠️ BATCH STATUS:", fileBatch.status);
      console.warn(`Completed: ${fileBatch.file_counts.completed}, Failed: ${fileBatch.file_counts.failed}`);
      
      return Response.json(
        { 
          success: fileBatch.file_counts.completed > 0,
          filesUploaded: fileBatch.file_counts.completed,
          filesFailed: fileBatch.file_counts.failed,
          status: fileBatch.status,
          warning: "Some files may have failed to process"
        },
        { status: fileBatch.file_counts.completed > 0 ? 200 : 500 }
      );
    }
    
  } catch (error) {
    console.error("\n========== ERROR in POST /api/assistants/files ==========");
    console.error("Error type:", error.constructor.name);
    console.error("Error message:", error.message);
    console.error("Error stack:", error.stack);
    console.error("========== END ERROR ==========\n");
    
    return Response.json(
      { error: error.message || "Failed to upload files" },
      { status: 500 }
    );
  }
}

// list files in assistant's vector store
export async function GET() {
  console.log("\n========== GET /api/assistants/files ==========");
  
  try {
    // Step 1: Validate Vector Store ID
    const vectorStoreId = process.env.OPENAI_VECTOR_STORE_ID;
    console.log("Step 1: Vector Store ID from env:", vectorStoreId);
    
    if (!vectorStoreId) {
      console.error("ERROR: Vector Store ID not found in environment variables");
      return Response.json(
        { error: "Vector Store ID not found. Please add OPENAI_VECTOR_STORE_ID to your .env file." },
        { status: 500 }
      );
    }

    // Step 2: List files in Vector Store
    console.log("Step 2: Calling openai.beta.vectorStores.files.list()...");
    const vectorFileList = await openai.beta.vectorStores.files.list(vectorStoreId);
    
    // Step 3: Log raw response
    console.log("Step 3: Raw Vector Store Files:", JSON.stringify(vectorFileList.data, null, 2));
    console.log("Number of files found:", vectorFileList.data.length);
    
    // Step 4: Check if empty
    if (!vectorFileList.data || vectorFileList.data.length === 0) {
      console.log("Vector store is EMPTY - returning empty array");
      return Response.json([]);
    }
    
    // Step 5: Map over files and get full details
    console.log("Step 5: Fetching detailed info for each file...");
    const filesWithDetails = await Promise.all(
      vectorFileList.data.map(async (vectorFile, index) => {
        console.log(`  [${index + 1}] Processing file ID: ${vectorFile.id}`);
        
        try {
          // Get full file details from OpenAI Files API
          const fileDetails = await openai.files.retrieve(vectorFile.id);
          console.log(`      ├─ Filename: ${fileDetails.filename}`);
          console.log(`      ├─ Status: ${vectorFile.status}`);
          console.log(`      └─ Created: ${new Date(fileDetails.created_at * 1000).toISOString()}`);
          
          return {
            file_id: vectorFile.id,
            filename: fileDetails.filename,
            status: vectorFile.status,
          };
        } catch (fileError) {
          console.error(`      └─ ERROR retrieving file ${vectorFile.id}:`, fileError.message);
          // Return partial info if file details fail
          return {
            file_id: vectorFile.id,
            filename: `Unknown (${vectorFile.id})`,
            status: vectorFile.status || 'error',
          };
        }
      })
    );
    
    // Step 6: Return results
    console.log("Step 6: Final files array being returned:");
    console.log(JSON.stringify(filesWithDetails, null, 2));
    console.log("========== END GET /api/assistants/files ==========\n");
    
    return Response.json(filesWithDetails);
    
  } catch (error) {
    console.error("\n========== ERROR in GET /api/assistants/files ==========");
    console.error("Error type:", error.constructor.name);
    console.error("Error message:", error.message);
    console.error("Error stack:", error.stack);
    console.error("Full error object:", JSON.stringify(error, null, 2));
    console.error("========== END ERROR ==========\n");
    
    return Response.json(
      { 
        error: error.message || "Failed to fetch files",
        details: error.stack 
      },
      { status: 500 }
    );
  }
}

// delete file from assistant's vector store
export async function DELETE(request) {
  try {
    // Check if OPENAI_VECTOR_STORE_ID is defined
    if (!process.env.OPENAI_VECTOR_STORE_ID) {
      console.error("Vector Store ID not found in environment variables");
      return Response.json(
        { error: "Vector Store ID not found" },
        { status: 500 }
      );
    }

    const body = await request.json();
    const fileId = body.fileId;

    if (!fileId) {
      return Response.json(
        { error: "File ID is required" },
        { status: 400 }
      );
    }

    const vectorStoreId = process.env.OPENAI_VECTOR_STORE_ID;
    await openai.beta.vectorStores.files.del(vectorStoreId, fileId); // delete file from vector store

    console.log("File deleted successfully:", fileId);

    return Response.json({ success: true });
  } catch (error) {
    console.error("Error deleting file:", error);
    console.error("Error details:", error.message);
    
    return Response.json(
      { error: error.message || "Failed to delete file" },
      { status: 500 }
    );
  }
}

