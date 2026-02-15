"""
Bulk upload PDF files to the AI Geotechnical Chat RAG system.
This script uploads all PDF files from a folder to the backend for processing.
"""

import os
import requests
import time
from pathlib import Path

# ============================================================================
# CONFIGURATION - Update these paths for your setup
# ============================================================================

FOLDER_PATH = r"C:\Path\To\Your\PDFs"  # Change this to your PDF folder
API_URL = "http://127.0.0.1:8000/api/upload"

# Optional: Set to True to skip files that are already uploaded
SKIP_EXISTING = False

# Optional: Add delay between uploads (seconds) to avoid overwhelming the server
DELAY_BETWEEN_UPLOADS = 2

# ============================================================================
# Main Script
# ============================================================================

def get_existing_files(api_base_url: str) -> set:
    """
    Get list of files already uploaded to the system.
    Returns a set of filenames.
    """
    try:
        list_url = api_base_url.replace("/upload", "/files")
        response = requests.get(list_url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Extract filenames from the response
            existing = {item.get('filename', '') for item in data.get('files', [])}
            print(f"[INFO] Found {len(existing)} existing files in database")
            return existing
        else:
            print(f"[WARNING] Could not fetch existing files: {response.status_code}")
            return set()
    except Exception as e:
        print(f"[WARNING] Error fetching existing files: {e}")
        return set()


def ingest_documents():
    """
    Upload all PDF files from FOLDER_PATH to the API endpoint.
    """
    print("=" * 70)
    print("PDF Bulk Upload - AI Geotechnical Chat")
    print("=" * 70)
    
    # Validate folder path
    if not os.path.exists(FOLDER_PATH):
        print(f"\n[ERROR] Folder does not exist: {FOLDER_PATH}")
        print("\nPlease update FOLDER_PATH in the script to point to your PDF folder.")
        return
    
    if not os.path.isdir(FOLDER_PATH):
        print(f"\n[ERROR] Path is not a directory: {FOLDER_PATH}")
        return
    
    print(f"\n[INFO] Scanning folder: {FOLDER_PATH}")
    print(f"[INFO] API endpoint: {API_URL}")
    
    # Get list of existing files if SKIP_EXISTING is enabled
    existing_files = set()
    if SKIP_EXISTING:
        print("\n[INFO] Checking for existing files...")
        existing_files = get_existing_files(API_URL)
    
    # Find all PDF files
    pdf_files = []
    for filename in os.listdir(FOLDER_PATH):
        if filename.lower().endswith('.pdf'):
            pdf_files.append(filename)
    
    if not pdf_files:
        print("\n[WARNING] No PDF files found in the folder!")
        return
    
    print(f"\n[INFO] Found {len(pdf_files)} PDF files")
    print("-" * 70)
    
    # Upload statistics
    successful = 0
    failed = 0
    skipped = 0
    failed_files = []
    
    # Upload each file
    for idx, filename in enumerate(pdf_files, 1):
        file_path = os.path.join(FOLDER_PATH, filename)
        
        # Skip if file already exists
        if SKIP_EXISTING and filename in existing_files:
            print(f"\n[{idx}/{len(pdf_files)}] ⏭️  SKIPPED: {filename}")
            print("         (Already in database)")
            skipped += 1
            continue
        
        print(f"\n[{idx}/{len(pdf_files)}] 📄 Uploading: {filename}")
        
        try:
            # Get file size for display
            file_size = os.path.getsize(file_path)
            file_size_mb = file_size / (1024 * 1024)
            print(f"         Size: {file_size_mb:.2f} MB")
            
            # Open and upload the file
            with open(file_path, 'rb') as f:
                files = {'file': (filename, f, 'application/pdf')}
                data = {'category': 'knowledge_base'}  # Tag as knowledge base
                
                # Send POST request with category
                start_time = time.time()
                response = requests.post(
                    API_URL,
                    files=files,
                    data=data,  # Include category in form data
                    timeout=300  # 5 minute timeout for large files
                )
                elapsed_time = time.time() - start_time
                
                # Check response
                if response.status_code == 200:
                    print(f"         ✅ Success (uploaded in {elapsed_time:.1f}s)")
                    successful += 1
                else:
                    error_msg = response.text
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('detail', error_msg)
                    except:
                        pass
                    
                    print(f"         ❌ Failed: HTTP {response.status_code}")
                    print(f"         Error: {error_msg}")
                    failed += 1
                    failed_files.append((filename, error_msg))
            
            # Add delay between uploads if configured
            if DELAY_BETWEEN_UPLOADS > 0 and idx < len(pdf_files):
                time.sleep(DELAY_BETWEEN_UPLOADS)
                
        except FileNotFoundError:
            print(f"         ❌ Failed: File not found")
            failed += 1
            failed_files.append((filename, "File not found"))
            
        except requests.exceptions.Timeout:
            print(f"         ❌ Failed: Upload timeout (file too large or server not responding)")
            failed += 1
            failed_files.append((filename, "Timeout"))
            
        except requests.exceptions.ConnectionError:
            print(f"         ❌ Failed: Could not connect to server")
            print(f"         Make sure the backend is running at {API_URL}")
            failed += 1
            failed_files.append((filename, "Connection error"))
            
        except Exception as e:
            print(f"         ❌ Failed: {str(e)}")
            failed += 1
            failed_files.append((filename, str(e)))
    
    # Print summary
    print("\n" + "=" * 70)
    print("UPLOAD SUMMARY")
    print("=" * 70)
    print(f"Total files found:    {len(pdf_files)}")
    print(f"Successfully uploaded: {successful}")
    print(f"Failed:               {failed}")
    if SKIP_EXISTING:
        print(f"Skipped (existing):   {skipped}")
    print("-" * 70)
    
    # Show failed files if any
    if failed_files:
        print("\nFailed uploads:")
        for filename, error in failed_files:
            print(f"  ❌ {filename}")
            print(f"     Error: {error}")
    
    # Show next steps
    if successful > 0:
        print("\n✅ Upload complete!")
        print(f"\n{successful} files were successfully uploaded and are being processed.")
        print("Processing includes:")
        print("  - Text extraction from PDFs")
        print("  - Chunking text into smaller segments")
        print("  - Generating vector embeddings")
        print("  - Storing in MongoDB")
        print("\nThis happens in the background and may take a few minutes.")
        print("\nYou can now:")
        print("  1. Check the frontend to see uploaded files")
        print("  2. Start asking questions about the documents")
        print("  3. Run 'python check_messages.py' to verify database")
    
    if failed > 0:
        print(f"\n⚠️  {failed} files failed to upload. Check the errors above.")
        print("\nCommon fixes:")
        print("  - Make sure the backend server is running")
        print("  - Check if files are valid PDFs")
        print("  - Verify file permissions")
        print("  - Check server logs for detailed errors")


def main():
    """Entry point with pre-flight checks"""
    print("\nStarting bulk PDF upload...")
    
    # Check if configuration has been updated
    if FOLDER_PATH == r"C:\Path\To\Your\PDFs":
        print("\n⚠️  WARNING: You haven't updated the FOLDER_PATH!")
        print("\nPlease edit this script and update:")
        print("  FOLDER_PATH = r\"C:\\Your\\Actual\\Path\\To\\PDFs\"")
        print("\nExample:")
        print("  FOLDER_PATH = r\"C:\\Users\\dharu\\Documents\\Geotechnical PDFs\"")
        return
    
    # Check if server is reachable
    print("\n[INFO] Checking if backend server is running...")
    try:
        health_url = API_URL.replace("/api/upload", "/")
        response = requests.get(health_url, timeout=5)
        if response.status_code == 200:
            print("[OK] Backend server is running")
        else:
            print(f"[WARNING] Server responded with status {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("\n[ERROR] Cannot connect to backend server!")
        print(f"        Expected at: {API_URL}")
        print("\nPlease start the backend server:")
        print("  cd python_backend")
        print("  python start_simple.py")
        return
    except Exception as e:
        print(f"[WARNING] Could not check server status: {e}")
    
    # Run the upload
    ingest_documents()


if __name__ == "__main__":
    main()
