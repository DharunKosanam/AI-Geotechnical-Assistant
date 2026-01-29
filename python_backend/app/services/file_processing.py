"""
File processing service for image conversion and handling
"""
import io
from PIL import Image
import img2pdf
from fastapi import HTTPException, status


async def convert_image_to_pdf(file_content: bytes, filename: str) -> tuple[bytes, str]:
    """
    Convert TIS/TIF/TIFF image to PDF
    
    Args:
        file_content: Raw file bytes
        filename: Original filename
        
    Returns:
        Tuple of (converted_content, new_filename)
    """
    try:
        # Verify it's a valid image using Pillow
        image = Image.open(io.BytesIO(file_content))
        
        # Convert to RGB if necessary (some formats need this)
        if image.mode not in ('RGB', 'L', '1'):
            image = image.convert('RGB')
        
        # Save image to bytes
        img_bytes = io.BytesIO()
        image.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        
        # Convert to PDF
        pdf_bytes = img2pdf.convert(img_bytes.read())
        
        # Update filename
        new_filename = filename.rsplit('.', 1)[0] + '.pdf'
        
        return pdf_bytes, new_filename
        
    except Exception as conv_error:
        print(f"❌ Error converting image to PDF: {conv_error}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to convert image to PDF: {str(conv_error)}"
        )


def needs_image_conversion(filename: str) -> bool:
    """
    Check if file needs conversion based on extension
    
    Args:
        filename: The filename to check
        
    Returns:
        True if file should be converted to PDF
    """
    if not filename or '.' not in filename:
        return False
    
    file_extension = filename.lower().split('.')[-1]
    return file_extension in ['tis', 'tif', 'tiff']


def determine_media_type(filename: str) -> str:
    """
    Determine media type based on file extension
    
    Args:
        filename: The filename
        
    Returns:
        Media type string
    """
    filename_lower = filename.lower()
    
    if filename_lower.endswith('.pdf'):
        return "application/pdf"
    elif filename_lower.endswith(('.png', '.jpg', '.jpeg')):
        if filename_lower.endswith('.png'):
            return "image/png"
        else:
            return "image/jpeg"
    elif filename_lower.endswith('.gif'):
        return "image/gif"
    elif filename_lower.endswith('.webp'):
        return "image/webp"
    elif filename_lower.endswith(('.doc', '.docx')):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif filename_lower.endswith(('.xls', '.xlsx')):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif filename_lower.endswith('.txt'):
        return "text/plain"
    else:
        return "application/octet-stream"

