import React, { useState, useEffect } from "react";
import styles from "./file-viewer.module.css";

const BACKEND_URL = process.env.NEXT_PUBLIC_PYTHON_API_URL || "http://127.0.0.1:8000";

const TrashIcon = () => (
  <svg
    className={styles.fileDeleteIcon}
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 12 12"
    height="12"
    width="12"
    fill="#353740"
  >
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M5.15736 1.33332C4.8911 1.33332 4.65864 1.51361 4.59238 1.77149L4.4214 2.43693H7.58373L7.41275 1.77149C7.34649 1.51361 7.11402 1.33332 6.84777 1.33332H5.15736ZM8.78829 2.43693L8.54271 1.48115C8.34393 0.707516 7.64653 0.166656 6.84777 0.166656H5.15736C4.35859 0.166656 3.6612 0.707515 3.46241 1.48115L3.21683 2.43693H1.33333C1.01117 2.43693 0.75 2.6981 0.75 3.02026C0.75 3.34243 1.01117 3.6036 1.33333 3.6036H1.39207L2.10068 10.2683C2.19529 11.1582 2.94599 11.8333 3.84087 11.8333H8.15913C9.05401 11.8333 9.80471 11.1582 9.89932 10.2683L10.6079 3.6036H10.6667C10.9888 3.6036 11.25 3.34243 11.25 3.02026C11.25 2.6981 10.9888 2.43693 10.6667 2.43693H8.78829ZM9.43469 3.6036H2.56531L3.2608 10.145C3.29234 10.4416 3.54257 10.6667 3.84087 10.6667H8.15913C8.45743 10.6667 8.70766 10.4416 8.7392 10.145L9.43469 3.6036ZM4.83333 4.83332C5.1555 4.83332 5.41667 5.09449 5.41667 5.41666V8.33332C5.41667 8.65549 5.1555 8.91666 4.83333 8.91666C4.51117 8.91666 4.25 8.65549 4.25 8.33332V5.41666C4.25 5.09449 4.51117 4.83332 4.83333 4.83332ZM7.16667 4.83332C7.48883 4.83332 7.75 5.09449 7.75 5.41666V8.33332C7.75 8.65549 7.48883 8.91666 7.16667 8.91666C6.8445 8.91666 6.58333 8.65549 6.58333 8.33332V5.41666C6.58333 5.09449 6.8445 4.83332 7.16667 4.83332Z"
    />
  </svg>
);

interface FileData {
  file_id: string;
  filename: string;
  status?: string;
}

const FileViewer = () => {
  const [files, setFiles] = useState<FileData[]>([]);
  const [optimisticFiles, setOptimisticFiles] = useState<FileData[]>([]); // Track optimistically added files

  useEffect(() => {
      fetchFiles();
  }, []);

  const fetchFiles = async () => {
    try {
      console.log("Fetching user uploaded files only...");
      // Use Python backend directly (same as upload and delete)
      const resp = await fetch(`${BACKEND_URL}/api/assistants/files?category=user_upload`, {
        method: "GET",
      });
      
      if (!resp.ok) {
        console.error(`Failed to fetch files. Status: ${resp.status}`);
        // Don't clear optimistic files on error
        return;
      }
      
      // Read response as text first to handle empty responses
      const responseText = await resp.text();
      
      if (!responseText || responseText.trim() === '') {
        console.warn("Empty response from files API - keeping optimistic files");
        // Don't clear optimistic files if API returns empty (race condition)
        return;
      }
      
      try {
        const data = JSON.parse(responseText);
        console.log("Fetched files:", data);
        
        // Handle both response formats: {files: [...]} and [...]
        const filesList = Array.isArray(data) ? data : (data.files || []);
        setFiles(filesList);
        
        // Remove optimistic files that now exist in the API response
        setOptimisticFiles(prevOptimistic => {
          return prevOptimistic.filter(optimisticFile => {
            // Keep optimistic file if it's not yet in the API response
            const existsInApi = filesList.some(apiFile => 
              apiFile.filename === optimisticFile.filename
            );
            if (existsInApi) {
              console.log(`Optimistic file confirmed: ${optimisticFile.filename}`);
            }
            return !existsInApi;
          });
        });
      } catch (jsonError) {
        console.error("Failed to parse JSON response:", jsonError);
        console.error(`Response text: ${responseText}`);
      }
    } catch (error) {
      console.error("Error fetching files:", error);
    }
  };

  const handleFileDelete = async (fileId: string, filename: string) => {
    try {
      // Show confirmation dialog
      const confirmed = window.confirm(
        `Are you sure you want to delete "${filename}"?\n\nThis will permanently remove the file and all its vector embeddings from the database.`
      );
      
      if (!confirmed) {
        console.log("File deletion cancelled by user");
        return;
      }
      
      // Check if this is an optimistic file (temp ID)
      if (fileId.startsWith('temp-')) {
        console.log("Removing optimistic file:", fileId);
        setOptimisticFiles(prev => prev.filter(f => f.file_id !== fileId));
        return;
      }
      
      console.log(`Deleting file: ${filename}`);
      
      // Call the new DELETE endpoint with filename (note: /api/files/delete/ prefix)
      const resp = await fetch(`${BACKEND_URL}/api/files/delete/${encodeURIComponent(filename)}`, {
        method: "DELETE",
        headers: {
          "Content-Type": "application/json",
        },
      });
      
      if (!resp.ok) {
        const errorData = await resp.json().catch(() => ({ message: "Unknown error" }));
        console.error(`Failed to delete file. Status: ${resp.status}`, errorData);
        alert(`Failed to delete file: ${errorData.detail || errorData.message || "Unknown error"}`);
        return;
      }
      
      const result = await resp.json();
      console.log("Delete successful:", result);
      
      // Only remove from UI after successful backend deletion
      setFiles(prev => prev.filter(f => f.file_id !== fileId));
      setOptimisticFiles(prev => prev.filter(f => f.file_id !== fileId));
      
      // Show success message
      console.log(`✓ Successfully deleted ${result.deleted_count} chunks for file: ${filename}`);
      
      // Refresh list to ensure consistency
      fetchFiles();
    } catch (error) {
      console.error("Error deleting file:", error);
      alert("Failed to delete file. Please try again.");
    }
  };

  const handleFileUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    try {
      const selectedFiles = event.target.files;
      if (!selectedFiles || selectedFiles.length === 0) return;
      
      console.log(`Starting upload of ${selectedFiles.length} file(s)...`);
      
      const uploadedFiles = [];
      
      // Upload files ONE AT A TIME to /api/upload endpoint
      for (let i = 0; i < selectedFiles.length; i++) {
        const file = selectedFiles[i];
        console.log(`Uploading ${i + 1}/${selectedFiles.length}: ${file.name}`);
        
        // Create FormData for THIS file
        const data = new FormData();
        data.append("file", file); // Note: "file" (singular) for /upload endpoint
        data.append("category", "user_upload"); // Tag as user upload (not knowledge base)
        
        try {
          // Use the correct RAG ingestion endpoint
          const resp = await fetch(`${BACKEND_URL}/api/upload`, {
            method: "POST",
            body: data,
          });
          
          console.log(`  Response status: ${resp.status}`);
          
          if (!resp.ok) {
            const errorData = await resp.json();
            console.error(`  Failed to upload ${file.name}:`, errorData);
            alert(`Upload failed for ${file.name}: ${errorData.detail || 'Unknown error'}`);
            continue; // Skip to next file
          }
          
          const result = await resp.json();
          console.log(`  Upload successful!`, result);
          uploadedFiles.push(file);
          
          // Add optimistic file to UI
          setOptimisticFiles(prev => [...prev, {
            file_id: `temp-${Date.now()}-${i}`,
            filename: file.name,
            status: "⏳ Processing..." // Show processing status
          }]);
          
        } catch (error) {
          console.error(`  Error uploading ${file.name}:`, error);
          alert(`Upload failed for ${file.name}. Please try again.`);
        }
      }
      
      console.log(`Uploaded ${uploadedFiles.length}/${selectedFiles.length} files successfully`);
      
      // Reset file input to allow re-uploading
      event.target.value = "";
      
      // Wait for background processing, then refresh (try twice)
      console.log("Waiting for background processing...");
      setTimeout(() => {
        console.log("Refreshing file list (first check)...");
        fetchFiles();
      }, 3000);
      setTimeout(() => {
        console.log("Refreshing file list (second check)...");
        fetchFiles();
      }, 8000);
      
    } catch (error) {
      console.error("Error in file upload handler:", error);
      alert("Upload failed. Please try again.");
      // Reset file input on error
      event.target.value = "";
    }
  };

  // Merge optimistic files with real files
  const allFiles = [...optimisticFiles, ...files];
  
  return (
    <div className={styles.fileViewer}>
      <div
        className={`${styles.filesList} ${
          allFiles.length !== 0 ? styles.grow : ""
        }`}
      >
        {allFiles.length === 0 ? (
          <div className={styles.title}>Attach files to test file search</div>
        ) : (
          allFiles.map((file) => (
            <div key={file.file_id} className={styles.fileEntry}>
              <div className={styles.fileName}>
                <span className={styles.fileName}>{file.filename}</span>
                <span className={styles.fileStatus}>{file.status}</span>
              </div>
              <span 
                onClick={() => handleFileDelete(file.file_id, file.filename)}
                style={{ cursor: 'pointer' }}
                title={`Delete ${file.filename}`}
              >
                <TrashIcon />
              </span>
            </div>
          ))
        )}
      </div>
      <div className={styles.fileUploadContainer}>
        <label htmlFor="file-upload" className={styles.fileUploadBtn}>
          Attach files
        </label>
        <input
          type="file"
          id="file-upload"
          name="file-upload"
          className={styles.fileUploadInput}
          multiple
          onChange={handleFileUpload}
        />
      </div>
    </div>
  );
};

export default FileViewer;
