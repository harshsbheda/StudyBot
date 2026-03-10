import os
import PyPDF2
import docx
from PIL import Image

# Uncomment the line below if Tesseract is installed:
# import pytesseract
# pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


def get_file_type(filename: str) -> str:
    """Get file type from extension."""
    ext = os.path.splitext(filename)[1].lower()
    mapping = {
        ".pdf":  "pdf",
        ".docx": "docx",
        ".doc":  "docx",
        ".txt":  "txt",
        ".png":  "image",
        ".jpg":  "image",
        ".jpeg": "image",
        ".webp": "image",
        ".bmp":  "image",
    }
    return mapping.get(ext, "txt")


def extract_text(file_path: str, file_type: str) -> str:
    """Extract readable text from a file."""
    try:
        if file_type == "pdf":
            return _from_pdf(file_path)
        elif file_type == "docx":
            return _from_docx(file_path)
        elif file_type == "txt":
            return _from_txt(file_path)
        elif file_type == "image":
            return _from_image(file_path)
        return ""
    except Exception as e:
        print(f"[FileProcessor] Error extracting {file_type}: {e}")
        return ""


def _from_pdf(path: str) -> str:
    text_parts = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for i, page in enumerate(reader.pages):
            try:
                t = page.extract_text()
                if t and t.strip():
                    text_parts.append(t)
            except Exception as e:
                print(f"[FileProcessor] PDF page {i} error: {e}")
                continue
    return "\n".join(text_parts)


def _from_docx(path: str) -> str:
    doc = docx.Document(path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def _from_txt(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()


def _from_image(path: str) -> str:
    """Extract text from image using OCR. Requires Tesseract installed."""
    try:
        import pytesseract
        img = Image.open(path)
        return pytesseract.image_to_string(img)
    except ImportError:
        return "[Image uploaded — install Tesseract OCR to extract text from images]"
    except Exception as e:
        print(f"[FileProcessor] OCR error: {e}")
        return ""
